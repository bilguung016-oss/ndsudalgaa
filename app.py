from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
import json

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///survey.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

ADMIN_PASSWORD = "admin123"


class Survey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    questions = db.relationship('Question', backref='survey', lazy=True, cascade='all, delete-orphan')


class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    survey_id = db.Column(db.Integer, db.ForeignKey('survey.id'), nullable=False)
    text = db.Column(db.String(500), nullable=False)
    question_type = db.Column(db.String(20), default='single')  # single, multiple, text, rating
    options = db.Column(db.Text)  # JSON array of option strings
    order = db.Column(db.Integer, default=0)
    answers = db.relationship('Answer', backref='question', lazy=True, cascade='all, delete-orphan')

    def get_options(self):
        if self.options:
            return json.loads(self.options)
        return []


class Response(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    survey_id = db.Column(db.Integer, db.ForeignKey('survey.id'), nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    answers = db.relationship('Answer', backref='response', lazy=True, cascade='all, delete-orphan')


class Answer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    response_id = db.Column(db.Integer, db.ForeignKey('response.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    value = db.Column(db.Text)  # JSON for multiple choice, plain text for others


# --- Public routes ---

@app.route('/')
def index():
    surveys = Survey.query.filter_by(is_active=True).order_by(Survey.created_at.desc()).all()
    return render_template('index.html', surveys=surveys)


@app.route('/survey/<int:survey_id>', methods=['GET', 'POST'])
def take_survey(survey_id):
    survey = Survey.query.get_or_404(survey_id)
    if not survey.is_active:
        return render_template('closed.html', survey=survey)

    questions = Question.query.filter_by(survey_id=survey_id).order_by(Question.order).all()

    if request.method == 'POST':
        response = Response(survey_id=survey_id)
        db.session.add(response)
        db.session.flush()

        for question in questions:
            if question.question_type == 'multiple':
                values = request.form.getlist(f'q_{question.id}')
                value = json.dumps(values)
            else:
                value = request.form.get(f'q_{question.id}', '')
            answer = Answer(response_id=response.id, question_id=question.id, value=value)
            db.session.add(answer)

        db.session.commit()
        return redirect(url_for('thank_you', survey_id=survey_id))

    return render_template('survey.html', survey=survey, questions=questions)


@app.route('/survey/<int:survey_id>/thanks')
def thank_you(survey_id):
    survey = Survey.query.get_or_404(survey_id)
    return render_template('thanks.html', survey=survey)


# --- Admin routes ---

@app.route('/admin')
def admin_login_page():
    if session.get('admin'):
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html')


@app.route('/admin/login', methods=['POST'])
def admin_login():
    if request.form.get('password') == ADMIN_PASSWORD:
        session['admin'] = True
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html', error='Нууц үг буруу байна')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login_page'))


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login_page'))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    surveys = Survey.query.order_by(Survey.created_at.desc()).all()
    stats = {}
    for s in surveys:
        stats[s.id] = Response.query.filter_by(survey_id=s.id).count()
    return render_template('admin_dashboard.html', surveys=surveys, stats=stats)


@app.route('/admin/survey/new', methods=['GET', 'POST'])
@admin_required
def new_survey():
    if request.method == 'POST':
        survey = Survey(
            title=request.form['title'],
            description=request.form.get('description', '')
        )
        db.session.add(survey)
        db.session.commit()
        return redirect(url_for('edit_survey', survey_id=survey.id))
    return render_template('new_survey.html')


@app.route('/admin/survey/<int:survey_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_survey(survey_id):
    survey = Survey.query.get_or_404(survey_id)
    questions = Question.query.filter_by(survey_id=survey_id).order_by(Question.order).all()
    return render_template('edit_survey.html', survey=survey, questions=questions)


@app.route('/admin/survey/<int:survey_id>/add-question', methods=['POST'])
@admin_required
def add_question(survey_id):
    data = request.get_json()
    options = json.dumps(data.get('options', [])) if data.get('options') else None
    count = Question.query.filter_by(survey_id=survey_id).count()
    q = Question(
        survey_id=survey_id,
        text=data['text'],
        question_type=data.get('type', 'single'),
        options=options,
        order=count
    )
    db.session.add(q)
    db.session.commit()
    return jsonify({'id': q.id, 'text': q.text, 'type': q.question_type})


@app.route('/admin/question/<int:question_id>/delete', methods=['POST'])
@admin_required
def delete_question(question_id):
    q = Question.query.get_or_404(question_id)
    db.session.delete(q)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/admin/survey/<int:survey_id>/toggle', methods=['POST'])
@admin_required
def toggle_survey(survey_id):
    survey = Survey.query.get_or_404(survey_id)
    survey.is_active = not survey.is_active
    db.session.commit()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/survey/<int:survey_id>/delete', methods=['POST'])
@admin_required
def delete_survey(survey_id):
    survey = Survey.query.get_or_404(survey_id)
    db.session.delete(survey)
    db.session.commit()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/survey/<int:survey_id>/results')
@admin_required
def survey_results(survey_id):
    survey = Survey.query.get_or_404(survey_id)
    questions = Question.query.filter_by(survey_id=survey_id).order_by(Question.order).all()
    total_responses = Response.query.filter_by(survey_id=survey_id).count()

    results = []
    for q in questions:
        answers = Answer.query.filter_by(question_id=q.id).all()
        if q.question_type in ('single', 'multiple'):
            counts = {}
            for opt in q.get_options():
                counts[opt] = 0
            for a in answers:
                if q.question_type == 'multiple':
                    try:
                        vals = json.loads(a.value)
                        for v in vals:
                            counts[v] = counts.get(v, 0) + 1
                    except Exception:
                        pass
                else:
                    counts[a.value] = counts.get(a.value, 0) + 1
            results.append({'question': q, 'type': 'chart', 'counts': counts})
        elif q.question_type == 'rating':
            vals = [int(a.value) for a in answers if a.value.isdigit()]
            avg = round(sum(vals) / len(vals), 1) if vals else 0
            dist = {str(i): vals.count(i) for i in range(1, 6)}
            results.append({'question': q, 'type': 'rating', 'avg': avg, 'dist': dist, 'count': len(vals)})
        else:
            texts = [a.value for a in answers if a.value]
            results.append({'question': q, 'type': 'text', 'texts': texts})

    return render_template('results.html', survey=survey, results=results, total=total_responses)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5001)
