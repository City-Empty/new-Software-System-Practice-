from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import LoginManager, UserMixin, login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename, send_from_directory
import os
import datetime
from datetime import timedelta
from db import db  # 从独立文件导入数据库实例
from models import User, Course, Exam, Question, ExamResult, LearningProgress, ForumPost, ForumReply  # 导入模型类
from flask_migrate import Migrate  # 用于数据库迁移
from werkzeug.utils import send_from_directory
from sqlalchemy import JSON
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'eduhub-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///eduhub.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads/videos'
app.config['MATERIALS_FOLDER'] = 'uploads/materials'
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB
app.permanent_session_lifetime = timedelta(hours=1)

# 确保上传目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['MATERIALS_FOLDER'], exist_ok=True)

# 初始化数据库和迁移工具
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
migrate = Migrate(app, db)  # 数据库迁移初始化


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# 首页
@app.route('/')
def index():
    courses = Course.query.all()
    return render_template('index.html', courses=courses)


# 课程详情
@app.route('/course/<int:course_id>')
def course_detail(course_id):
    course = Course.query.get_or_404(course_id)
    return render_template('course_detail.html', course=course)


# 视频播放
@app.route('/video/<path:filename>')
def play_video(filename):
    try:
        return send_from_directory(
            app.config['UPLOAD_FOLDER'],
            filename,
            as_attachment=False
        )
    except FileNotFoundError:
        flash("视频文件不存在")
        return redirect(url_for('index'))
    except Exception as e:
        flash(f"播放错误: {str(e)}")
        return redirect(url_for('index'))


# 登录
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user, remember=True)
            flash('登录成功')
            return redirect(url_for('index'))
        else:
            flash('用户名或密码错误')
    return render_template('login.html')


# 注册
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        if User.query.filter_by(username=username).first():
            flash('用户名已存在')
            return redirect(url_for('register'))
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password, role=role)
        db.session.add(new_user)
        db.session.commit()
        flash('注册成功，请登录')
        return redirect(url_for('login'))
    return render_template('register.html')


# 登出
@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('已登出')
    return redirect(url_for('index'))


# 教师仪表盘
@app.route('/teacher/dashboard')
@login_required
def teacher_dashboard():
    if current_user.role != 'teacher':
        abort(403)
    courses = Course.query.filter_by(teacher_id=current_user.id).all()
    return render_template('teacher_dashboard.html', courses=courses)


# 教师课程管理 - 创建课程
@app.route('/teacher/course/create', methods=['GET', 'POST'])
@login_required
def create_course():
    if current_user.role != 'teacher':
        abort(403)
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        new_course = Course(
            title=title,
            description=description,
            teacher_id=current_user.id
        )
        db.session.add(new_course)
        db.session.commit()

        # 处理视频上传
        if 'video' in request.files:
            video_file = request.files['video']
            if video_file.filename != '':
                if allowed_file(video_file.filename):
                    filename = secure_filename(video_file.filename)
                    unique_filename = f"{os.urandom(16).hex()}_{filename}"
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    video_file.save(file_path)
                    new_course.video_filename = unique_filename
                    db.session.commit()

        flash('课程创建成功')
        return redirect(url_for('teacher_dashboard'))
    return render_template('teacher_course.html', course=None)


# 教师课程管理 - 编辑课程
@app.route('/teacher/course/<int:course_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_course(course_id):
    if current_user.role != 'teacher':
        abort(403)
    course = Course.query.get_or_404(course_id)
    if course.teacher_id != current_user.id:
        abort(403)
    if request.method == 'POST':
        course.title = request.form['title']
        course.description = request.form['description']

        # 处理视频更新
        if 'video' in request.files:
            video_file = request.files['video']
            if video_file.filename != '':
                if allowed_file(video_file.filename):
                    # 删除旧视频文件（如果有）
                    if course.video_filename:
                        old_file_path = os.path.join(app.config['UPLOAD_FOLDER'], course.video_filename)
                        if os.path.exists(old_file_path):
                            os.remove(old_file_path)

                    # 保存新视频
                    filename = secure_filename(video_file.filename)
                    unique_filename = f"{os.urandom(16).hex()}_{filename}"
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    video_file.save(file_path)
                    course.video_filename = unique_filename

        db.session.commit()
        flash('课程更新成功')
        return redirect(url_for('teacher_dashboard'))
    return render_template('teacher_course.html', course=course)


# 上传视频
@app.route('/teacher/course/<int:course_id>/upload', methods=['GET', 'POST'])
@login_required
def upload_video(course_id):
    if current_user.role != 'teacher':
        abort(403)
    course = Course.query.get_or_404(course_id)
    if course.teacher_id != current_user.id:
        abort(403)
    if request.method == 'POST':
        if 'video' not in request.files:
            flash('未选择视频文件')
            return redirect(request.url)
        video_file = request.files['video']
        if video_file.filename == '':
            flash('未选择视频文件')
            return redirect(request.url)
        if video_file and allowed_file(video_file.filename):
            filename = secure_filename(video_file.filename)
            unique_filename = f"{os.urandom(16).hex()}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            video_file.save(file_path)
            course.video_filename = unique_filename
            db.session.commit()
            flash('视频上传成功')
            return redirect(url_for('course_detail', course_id=course_id))
    return render_template('upload.html', course=course)


# 上传学习材料
@app.route('/teacher/course/<int:course_id>/upload_materials', methods=['GET', 'POST'])
@login_required
def upload_materials(course_id):
    if current_user.role != 'teacher':
        abort(403)
    course = Course.query.get_or_404(course_id)
    if course.teacher_id != current_user.id:
        abort(403)
    if request.method == 'POST':
        if 'materials' not in request.files:
            flash('未选择学习材料文件')
            return redirect(request.url)
        materials_file = request.files['materials']
        if materials_file.filename == '':
            flash('未选择学习材料文件')
            return redirect(request.url)
        if materials_file:
            filename = secure_filename(materials_file.filename)
            unique_filename = f"{os.urandom(16).hex()}_{filename}"
            file_path = os.path.join(app.config['MATERIALS_FOLDER'], unique_filename)
            materials_file.save(file_path)
            course.learning_materials = unique_filename
            db.session.commit()
            flash('学习材料上传成功')
            return redirect(url_for('course_detail', course_id=course_id))
    return render_template('upload_materials.html', course=course)


# 下载学习材料
@app.route('/materials/<path:filename>')
def download_materials(filename):
    return send_from_directory(app.config['MATERIALS_FOLDER'], filename, as_attachment=True)


# 考试管理 - 课程考试列表
@app.route('/teacher/course/<int:course_id>/exams')
@login_required
def teacher_course_exams(course_id):
    if current_user.role != 'teacher':
        abort(403)
    course = Course.query.get_or_404(course_id)
    if course.teacher_id != current_user.id:
        abort(403)
    exams = Exam.query.filter_by(course_id=course_id).all()
    return render_template('teacher_exam_management.html', course=course, exams=exams)

#考试创建
@app.route('/teacher/course/<int:course_id>/exam/create', methods=['GET', 'POST'])
@login_required
def create_exam(course_id):
    if current_user.role != 'teacher':
        abort(403)

    course = Course.query.get_or_404(course_id)

    if request.method == 'POST':
        title = request.form['title']
        duration = int(request.form.get('duration', 60))  # 默认60分钟

        # 创建考试
        new_exam = Exam(
            title=title,
            course_id=course_id,
            duration=duration
        )
        db.session.add(new_exam)
        db.session.commit()

        # 处理试题
        question_count = int(request.form.get('question_count', 0))
        for i in range(1, question_count + 1):
            question_text = request.form.get(f'question_{i}')
            option_a = request.form.get(f'option_a_{i}')
            option_b = request.form.get(f'option_b_{i}')
            option_c = request.form.get(f'option_c_{i}')
            option_d = request.form.get(f'option_d_{i}')
            correct_answer = request.form.get(f'correct_answer_{i}')
            score = int(request.form.get(f'score_{i}', 1))  # 默认1分

            if question_text and option_a and option_b and option_c and option_d and correct_answer:
                # 构建选项JSON
                options = {
                    'A': option_a,
                    'B': option_b,
                    'C': option_c,
                    'D': option_d
                }

                question = Question(
                    exam_id=new_exam.id,
                    question_text=question_text,
                    options=options,
                    correct_answer=correct_answer,
                    score=score
                )
                db.session.add(question)

        db.session.commit()
        flash('考试创建成功')
        return redirect(url_for('teacher_course_exams', course_id=course_id))

    return render_template('create_exam.html', course=course)


# 查看试题列表
@app.route('/teacher/exam/<int:exam_id>/questions')
@login_required
def view_questions(exam_id):
    if current_user.role != 'teacher':
        abort(403)

    exam = Exam.query.get_or_404(exam_id)
    questions = Question.query.filter_by(exam_id=exam_id).all()

    return render_template('view_questions.html', exam=exam, questions=questions)


# 添加试题
@app.route('/teacher/exam/<int:exam_id>/add_question', methods=['GET', 'POST'])
@login_required
def add_question(exam_id):
    if current_user.role != 'teacher':
        abort(403)

    exam = Exam.query.get_or_404(exam_id)

    if request.method == 'POST':
        question_text = request.form['question_text']
        option_a = request.form['option_a']
        option_b = request.form['option_b']
        option_c = request.form['option_c']
        option_d = request.form['option_d']
        correct_answer = request.form['correct_answer']
        score = int(request.form.get('score', 1))

        options = {
            'A': option_a,
            'B': option_b,
            'C': option_c,
            'D': option_d
        }

        new_question = Question(
            exam_id=exam_id,
            question_text=question_text,
            options=options,
            correct_answer=correct_answer,
            score=score
        )
        db.session.add(new_question)
        db.session.commit()

        flash('试题添加成功')
        return redirect(url_for('view_questions', exam_id=exam_id))

    return render_template('add_question.html', exam=exam)



# 查看学生数据
@app.route('/teacher/course/<int:course_id>/view_student_data')
@login_required
def view_student_data(course_id):
    if current_user.role != 'teacher':
        abort(403)
    course = Course.query.get_or_404(course_id)
    if course.teacher_id != current_user.id:
        abort(403)
    students = course.students.all()
    results = []
    for student in students:
        exam_results = db.session.query(ExamResult).join(Exam).filter(ExamResult.user_id == student.id, Exam.course_id == course_id).all()
        learning_progress = LearningProgress.query.filter_by(user_id=student.id, course_id=course_id).first()
        results.append({
            'student': student,
            'exam_results': exam_results,
            'learning_progress': learning_progress
        })
    return render_template('view_student_data.html', course=course, results=results)
#论坛
# 课程论坛
@app.route('/forum', methods=['GET', 'POST'])
@login_required
def course_forum():
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        new_post = ForumPost(
            title=title,
            content=content,
            user_id=current_user.id
        )
        db.session.add(new_post)
        db.session.commit()
        flash('帖子发布成功')
        return redirect(url_for('course_forum'))

    forum_posts = ForumPost.query.all()
    return render_template('course_forum.html', forum_posts=forum_posts)

@app.route('/forum/reply/<int:post_id>', methods=['POST'])
@login_required
def reply_to_post(post_id):
    content = request.form['content']
    new_reply = ForumReply(
        content=content,
        user_id=current_user.id,
        post_id=post_id
    )
    db.session.add(new_reply)
    db.session.commit()
    flash('回复成功')
    return redirect(url_for('course_forum'))


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'mp4', 'webm', 'ogg'}


# 学生参加考试
@app.route('/student/exam/<int:exam_id>/take', methods=['GET', 'POST'])
@login_required
def take_exam(exam_id):
    if current_user.role != 'student':
        abort(403)

    exam = Exam.query.get_or_404(exam_id)
    questions = Question.query.filter_by(exam_id=exam_id).all()

    if request.method == 'POST':
        total_score = 0
        total_possible_score = sum(q.score for q in questions)

        for question in questions:
            user_answer = request.form.get(f'question_{question.id}')
            if user_answer == question.correct_answer:
                total_score += question.score

        # 保存考试结果
        result = ExamResult(
            user_id=current_user.id,
            exam_id=exam_id,
            score=total_score,
            total_possible_score=total_possible_score
        )
        db.session.add(result)
        db.session.commit()

        flash('考试已提交，感谢参与！')
        return redirect(url_for('exam_result', result_id=result.id))

    return render_template('take_exam.html', exam=exam, questions=questions)


# 学生考试结果
@app.route('/student/exam/result/<int:result_id>')
@login_required
def exam_result(result_id):
    if current_user.role != 'student':
        abort(403)

    result = ExamResult.query.get_or_404(result_id)
    exam = result.exam
    # 确保查看的是自己的考试结果
    if result.user_id != current_user.id:
        abort(403)

    return render_template('exam_result.html', result=result)


# 学生学习进度
@app.route('/student/progress')
@login_required
def learning_progress():
    if current_user.role != 'student':
        abort(403)

    # 获取所有课程和学习进度
    courses = Course.query.all()
    progress_records = LearningProgress.query.filter_by(user_id=current_user.id).all()

    # 构建课程与进度的映射
    progress_map = {record.course_id: record for record in progress_records}

    return render_template('learning_progress.html', courses=courses, progress_map=progress_map)

@app.route('/student/courses')
@login_required
def student_courses():
    if current_user.role != 'student':
        abort(403)
    courses = current_user.enrolled_courses
    # 构建 results_map
    results_map = {}
    exam_results = ExamResult.query.filter_by(user_id=current_user.id).all()
    for result in exam_results:
        results_map[(result.exam_id, current_user.id)] = result
    return render_template('student_courses.html', courses=courses, results_map=results_map)


@app.route('/student/exams')
@login_required
def student_exams():
    if current_user.role != 'student':
        abort(403)
    courses = current_user.enrolled_courses
    # 构建 results_map
    results_map = {}
    exam_results = ExamResult.query.filter_by(user_id=current_user.id).all()
    for result in exam_results:
        results_map[(result.exam_id, current_user.id)] = result
    return render_template('student_exams.html', courses=courses, results_map=results_map)

@app.route('/student/course/enroll/<int:course_id>', methods=['GET'])
@login_required
def enroll_course(course_id):
    if current_user.role != 'student':
        abort(403)
    course = Course.query.get_or_404(course_id)
    current_user.enrolled_courses.append(course)
    db.session.commit()
    flash('选课成功')
    return redirect(url_for('student_courses'))

@app.route('/teacher/course/<int:course_id>/students_progress', methods=['GET'])
@login_required
def teacher_students_progress(course_id):
    if current_user.role != 'teacher':
        abort(403)
    course = Course.query.get_or_404(course_id)
    students = course.students.all()
    student_progress = []
    for student in students:
        progress = LearningProgress.query.filter_by(user_id=student.id, course_id=course.id).first()
        exam_results = ExamResult.query.filter_by(user_id=student.id).join(Exam).filter(Exam.course_id==course.id).all()
        student_progress.append({
            'student': student,
            'progress': progress,
            'exam_results': exam_results
        })
    return render_template('teacher_students_progress.html', course=course, student_progress=student_progress)


# 教师查看所有考试
@app.route('/teacher/all_exams')
@login_required
def teacher_all_exams():
    if current_user.role != 'teacher':
        abort(403)
    # 获取当前教师所有课程的所有考试
    courses = Course.query.filter_by(teacher_id=current_user.id).all()
    exams = []
    for course in courses:
        exams.extend(course.exams)
    return render_template('teacher_all_exams.html', exams=exams)



if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)