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
from flask import  send_from_directory, request
from sqlalchemy import JSON
import json
from sqlalchemy.exc import IntegrityError
from functools import wraps
from sqlalchemy.orm import joinedload

app = Flask(__name__)
app.config['SECRET_KEY'] = 'eduhub-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///eduhub.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads/videos'
app.config['MATERIALS_FOLDER'] = 'uploads/materials'
app.config['COVER_FOLDER'] = 'uploads/covers'
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB
app.permanent_session_lifetime = timedelta(hours=1)

# 确保上传目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['MATERIALS_FOLDER'], exist_ok=True)
os.makedirs(app.config['COVER_FOLDER'], exist_ok=True)

# 初始化数据库和迁移工具
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
migrate = Migrate(app, db)  # 数据库迁移初始化


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# 新增：学生端课程访问权限检查装饰器
def student_course_active_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        course_id = kwargs.get('course_id')
        if course_id:
            course = Course.query.get(course_id)
            if course and course.is_ended:
                flash('课程已结束，无法继续学习。')
                return redirect(url_for('student_courses'))
        return f(*args, **kwargs)
    return decorated_function


# 首页
@app.route('/', methods=['GET'])
def index():
    title = request.args.get('title', '').strip()
    teacher = request.args.get('teacher', '').strip()
    is_ended = request.args.get('is_ended')

    query = Course.query
    if title:
        query = query.filter(Course.title.contains(title))
    if teacher:
        query = query.join(User).filter(User.username.contains(teacher))
    if is_ended in ['0', '1']:
        query = query.filter(Course.is_ended == (is_ended == '1'))

    courses = query.all()
    return render_template('index.html', courses=courses)

# 封面图片访问
@app.route('/cover/<path:filename>')
def cover_image(filename):
    return send_from_directory(app.config['COVER_FOLDER'], filename)


# 课程详情
@app.route('/course/<int:course_id>')
def course_detail(course_id):
    course = Course.query.get_or_404(course_id)
    material_files = []
    if course.learning_materials:
        try:
            material_files = json.loads(course.learning_materials)
        except Exception:
            material_files = []
    # 判断封面
    if course.cover_image:
        cover_url = url_for('cover_image', filename=course.cover_image)
    else:
        cover_url = url_for('static', filename='default_cover.png')
    return render_template('course_detail.html', course=course, material_files=material_files, cover_url=cover_url)

# 视频播放
@app.route('/video/<path:filename>')
@login_required
def play_video(filename):
    # 限制学生访问已结束课程的视频
    if current_user.role == 'student':
        # 通过视频文件名反查课程
        course = Course.query.filter_by(video_filename=filename).first()
        if course and course.is_ended:
            flash('课程已结束，无法观看视频。')
            return redirect(url_for('student_courses'))
    try:
        return send_from_directory(
            app.config['UPLOAD_FOLDER'],
            filename,
            environ=request.environ
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
    # 按未结束(0) > 已结束(1)排序
    def sort_key(course):
        return 1 if course.is_ended else 0
    courses = sorted(courses, key=sort_key)
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

        # 处理封面图片上传
        if 'cover' in request.files:
            cover_file = request.files['cover']
            if cover_file and cover_file.filename != '':
                if allowed_image(cover_file.filename):
                    cover_filename = secure_filename(cover_file.filename)
                    unique_cover_filename = f"{os.urandom(16).hex()}_{cover_filename}"
                    cover_path = os.path.join(app.config['COVER_FOLDER'], unique_cover_filename)
                    cover_file.save(cover_path)
                    new_course.cover_image = unique_cover_filename
                    db.session.commit()

        # 处理多个学习资料上传及说明
        materials_info = []
        if 'materials' in request.files:
            materials_files = request.files.getlist('materials')
            descs = request.form.getlist('material_desc')
            for idx, materials_file in enumerate(materials_files):
                if materials_file and materials_file.filename != '':
                    filename = secure_filename(materials_file.filename)
                    unique_filename = f"{os.urandom(16).hex()}_{filename}"
                    file_path = os.path.join(app.config['MATERIALS_FOLDER'], unique_filename)
                    materials_file.save(file_path)
                    desc = descs[idx] if idx < len(descs) else ''
                    materials_info.append({'filename': unique_filename, 'desc': desc})
        if materials_info:
            new_course.learning_materials = json.dumps(materials_info, ensure_ascii=False)
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

        # 处理封面图片更新
        if 'cover' in request.files:
            cover_file = request.files['cover']
            if cover_file and cover_file.filename != '':
                if allowed_image(cover_file.filename):
                    # 删除旧封面
                    if course.cover_image:
                        old_cover_path = os.path.join(app.config['COVER_FOLDER'], course.cover_image)
                        if os.path.exists(old_cover_path):
                            os.remove(old_cover_path)
                    cover_filename = secure_filename(cover_file.filename)
                    unique_cover_filename = f"{os.urandom(16).hex()}_{cover_filename}"
                    cover_path = os.path.join(app.config['COVER_FOLDER'], unique_cover_filename)
                    cover_file.save(cover_path)
                    course.cover_image = unique_cover_filename

        # 处理多个学习资料上传及说明（追加到已有资料）
        materials_info = []
        # 先读取已有资料
        if course.learning_materials:
            try:
                materials_info = json.loads(course.learning_materials)
            except Exception:
                materials_info = []
        # 处理新上传
        if 'materials' in request.files:
            materials_files = request.files.getlist('materials')
            descs = request.form.getlist('material_desc')
            for idx, materials_file in enumerate(materials_files):
                if materials_file and materials_file.filename != '':
                    filename = secure_filename(materials_file.filename)
                    unique_filename = f"{os.urandom(16).hex()}_{filename}"
                    file_path = os.path.join(app.config['MATERIALS_FOLDER'], unique_filename)
                    materials_file.save(file_path)
                    desc = descs[idx] if idx < len(descs) else ''
                    materials_info.append({'filename': unique_filename, 'desc': desc})
        # 处理资料说明修改
        if 'existing_desc' in request.form:
            existing_descs = request.form.getlist('existing_desc')
            for i, desc in enumerate(existing_descs):
                if i < len(materials_info):
                    materials_info[i]['desc'] = desc
        # 处理资料删除
        del_indices = request.form.getlist('delete_material')
        del_indices = set(int(i) for i in del_indices)
        new_materials_info = []
        for idx, item in enumerate(materials_info):
            if idx not in del_indices:
                new_materials_info.append(item)
            else:
                # 删除文件
                file_path = os.path.join(app.config['MATERIALS_FOLDER'], item['filename'])
                if os.path.exists(file_path):
                    os.remove(file_path)
        course.learning_materials = json.dumps(new_materials_info, ensure_ascii=False)
        db.session.commit()
        flash('课程更新成功')
        return redirect(url_for('teacher_dashboard'))
    # 展示时解析资料
    materials_info = []
    if course.learning_materials:
        try:
            materials_info = json.loads(course.learning_materials)
        except Exception:
            materials_info = []
    return render_template('teacher_course.html', course=course, materials_info=materials_info)


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
@login_required
def download_materials(filename):
    if current_user.role == 'student':
        # 通过材料文件名反查课程
        courses = Course.query.all()
        for course in courses:
            if course.learning_materials:
                try:
                    materials = json.loads(course.learning_materials)
                    if isinstance(materials, list):
                        if any(item['filename'] == filename for item in materials):
                            if course.is_ended:
                                flash('课程已结束，无法下载材料。')
                                return redirect(url_for('student_courses'))
                    elif isinstance(materials, str):
                        if materials == filename and course.is_ended:
                            flash('课程已结束，无法下载材料。')
                            return redirect(url_for('student_courses'))
                except Exception:
                    continue
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
            question_type = request.form.get(f'question_type_{i}', 'single')
            score = int(request.form.get(f'score_{i}', 1))
            options = None
            correct_answer = None
            explanation = request.form.get(f'explanation_{i}', '')

            # 处理图片上传
            image_filename = None
            image_field = f'image_{i}'
            if image_field in request.files:
                image_file = request.files[image_field]
                if image_file and image_file.filename != '' and allowed_image(image_file.filename):
                    filename = secure_filename(image_file.filename)
                    unique_filename = f"{os.urandom(16).hex()}_{filename}"
                    image_path = os.path.join(app.config['COVER_FOLDER'], unique_filename)
                    image_file.save(image_path)
                    image_filename = unique_filename

            if question_type in ['single', 'multiple']:
                options = {
                    'A': request.form.get(f'option_a_{i}', ''),
                    'B': request.form.get(f'option_b_{i}', ''),
                    'C': request.form.get(f'option_c_{i}', ''),
                    'D': request.form.get(f'option_d_{i}', '')
                }
                if question_type == 'single':
                    correct_answer = request.form.get(f'correct_answer_{i}', '')
                elif question_type == 'multiple':
                    correct_answer_list = request.form.getlist(f'correct_answer_multi_{i}')
                    correct_answer = ','.join(sorted([x for x in correct_answer_list if x]))
            elif question_type == 'judge':
                # 判断题固定选项
                options = {'A': '正确', 'B': '错误'}
                correct_answer = request.form.get(f'judge_answer{i}', '')
            elif question_type == 'blank':
                correct_answer = request.form.get(f'blank_answer_{i}', '')
            elif question_type == 'short':
                correct_answer = request.form.get(f'short_answer_{i}', '')

            if question_text and (options or question_type in ['blank', 'short']) and correct_answer is not None:
                question = Question(
                    exam_id=new_exam.id,
                    question_text=question_text,
                    options=options,
                    correct_answer=correct_answer,
                    score=score,
                    question_type=question_type,
                    explanation=explanation,
                    image=image_filename
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
    # 权限检查
    if current_user.role != 'teacher':
        abort(403)
    exam = Exam.query.get_or_404(exam_id)
    if request.method == 'POST':
        question_text = request.form['question_text']
        question_type = request.form.get('question_type', 'single')
        score = int(request.form.get('score', 1))
        explanation = request.form.get('explanation', '')

        options = None
        correct_answer = None

        # 处理图片上传
        image_filename = None
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file and image_file.filename != '' and allowed_image(image_file.filename):
                filename = secure_filename(image_file.filename)
                unique_filename = f"{os.urandom(16).hex()}_{filename}"
                image_path = os.path.join(app.config['COVER_FOLDER'], unique_filename)
                image_file.save(image_path)
                image_filename = unique_filename

        if question_type == 'single':
            options = {
                'A': request.form.get('option_a', ''),
                'B': request.form.get('option_b', ''),
                'C': request.form.get('option_c', ''),
                'D': request.form.get('option_d', '')
            }
            correct_answer = request.form.get('correct_answer', 'A')
        elif question_type == 'multiple':
            options = {
                'A': request.form.get('option_a', ''),
                'B': request.form.get('option_b', ''),
                'C': request.form.get('option_c', ''),
                'D': request.form.get('option_d', '')
            }
            correct_answer_list = request.form.getlist('correct_answer_multi')
            correct_answer = ','.join(sorted(correct_answer_list))
        elif question_type == 'blank':
            correct_answer = request.form.get('blank_answer', '')
        elif question_type == 'short':
            correct_answer = request.form.get('short_answer', '')
        elif question_type == 'judge':
            # 判断题固定选项
            options = {'A': '正确', 'B': '错误'}
            correct_answer = request.form.get('judge_answer', 'A')

        new_question = Question(
            exam_id=exam_id,
            question_text=question_text,
            options=options,
            correct_answer=correct_answer,
            score=score,
            explanation=explanation,
            question_type=question_type,
            image=image_filename
        )
        db.session.add(new_question)
        db.session.commit()
        flash('试题添加成功')
        return redirect(url_for('view_questions', exam_id=exam_id))
    return render_template('add_question.html', exam=exam, question=None)
# 编辑试题
@app.route('/teacher/exam/<int:exam_id>/question/<int:question_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_question(exam_id, question_id):
    # 权限检查
    if current_user.role != 'teacher':
        abort(403)
    exam = Exam.query.get_or_404(exam_id)
    question = Question.query.get_or_404(question_id)
    if question.exam_id != exam.id:
        abort(400)
    # 处理POST请求
    if request.method == 'POST':
        # 获取题目文本内容和类型
        question.question_text = request.form['question_text']
        question.question_type = request.form.get('question_type', 'single')
        if question.question_type in ['single', 'multiple']:
            question.options = {
                'A': request.form.get('option_a', ''),
                'B': request.form.get('option_b', ''),
                'C': request.form.get('option_c', ''),
                'D': request.form.get('option_d', '')
            }
            if question.question_type == 'single':
                question.correct_answer = request.form.get('correct_answer', 'A')
            else:
                # 多选题：先清空答案，再根据当前选项和表单选项更新
                correct_answer_list = request.form.getlist('correct_answer_multi')
                valid_keys = [k for k, v in question.options.items() if v and k in ['A', 'B', 'C', 'D']]
                filtered = [k for k in correct_answer_list if k in valid_keys]
                question.correct_answer = ','.join(sorted(filtered))
        elif question.question_type == 'judge':
            question.options = {'A': '正确', 'B': '错误'}
            question.correct_answer = request.form.get('correct_answer', 'A')
        elif question.question_type == 'blank':
            question.options = None
            question.correct_answer = request.form.get('blank_answer', '')
        elif question.question_type == 'short':
            question.options = None
            question.correct_answer = request.form.get('short_answer', '')

        question.score = int(request.form.get('score', 1))
        question.explanation = request.form.get('explanation')

        # 处理图片上传
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file and image_file.filename != '' and allowed_image(image_file.filename):
                # 删除旧图片
                if question.image:
                    old_path = os.path.join(app.config['COVER_FOLDER'], question.image)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                filename = secure_filename(image_file.filename)
                unique_filename = f"{os.urandom(16).hex()}_{filename}"
                image_path = os.path.join(app.config['COVER_FOLDER'], unique_filename)
                image_file.save(image_path)
                question.image = unique_filename

        db.session.commit()

        # 重新判分所有相关学生答卷
        rejudge_exam_results(exam_id)

        flash('试题修改成功，相关学生成绩已自动更新')
        return redirect(url_for('view_questions', exam_id=exam_id))
    return render_template('edit_question.html', exam=exam, question=question)


# 删除试题
@app.route('/teacher/exam/<int:exam_id>/question/<int:question_id>/delete', methods=['POST'])
@login_required
def delete_question(exam_id, question_id):
    if current_user.role != 'teacher':
        abort(403)
    exam = Exam.query.get_or_404(exam_id)
    question = Question.query.get_or_404(question_id)
    if question.exam_id != exam.id:
        abort(400)
    db.session.delete(question)
    db.session.commit()
    flash('试题已删除')
    return redirect(url_for('view_questions', exam_id=exam_id))


# 查看学生数
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
        image_filename = None
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file and image_file.filename != '' and allowed_image(image_file.filename):
                filename = secure_filename(image_file.filename)
                unique_filename = f"{os.urandom(16).hex()}_{filename}"
                image_path = os.path.join(app.config['COVER_FOLDER'], unique_filename)
                image_file.save(image_path)
                image_filename = unique_filename
        new_post = ForumPost(
            title=title,
            content=content,
            user_id=current_user.id,
            image=image_filename  # 记得模型要有 image 字段
        )
        db.session.add(new_post)
        db.session.commit()
        flash('帖子发布成功')
        return redirect(url_for('course_forum'))
    forum_posts = ForumPost.query.order_by(ForumPost.created_at.desc()).all()
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

def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'jpg', 'jpeg', 'png', 'gif'}


# 学生参加考试
@app.route('/student/exam/<int:exam_id>/take', methods=['GET', 'POST'])
@login_required
def take_exam(exam_id):
    if current_user.role != 'student':
        abort(403)

    exam = Exam.query.get_or_404(exam_id)
    course = exam.course
    if course.is_ended:
        flash('课程已结束，无法参加考试。')
        return redirect(url_for('student_courses'))

    questions = Question.query.filter_by(exam_id=exam_id).all()

    # 检查 options 是否为 None，若为 None 则赋予空字典
    for question in questions:
        if question.options is None:
            question.options = {}

    if request.method == 'POST':
        total_score = 0
        total_possible_score = sum(q.score for q in questions)
        student_answers = {}
        is_correct_map = {}
        for question in questions:
            qid = str(question.id)
            user_answer = ''
            if question.question_type == 'multiple':
                user_answer_raw = request.form.get(f'question_{qid}', '')
                user_answer = ','.join(sorted([ans.strip() for ans in user_answer_raw.split(',') if ans.strip() in ['A', 'B', 'C', 'D']]))
            elif question.question_type == 'judge':
                user_answer = request.form.get(f'question_{qid}', '').strip()
            else:
                user_answer = request.form.get(f'question_{qid}', '').strip()
            student_answers[qid] = user_answer

            # 判分并记录正误
            correct = False
            if question.question_type == 'multiple':
                correct = (
                    user_answer ==
                    ','.join(sorted([ans.strip() for ans in (question.correct_answer or '').split(',') if ans.strip() in ['A', 'B', 'C', 'D']]))
                )
                if correct:
                    total_score += question.score
            elif question.question_type == 'judge':
                correct = user_answer == (question.correct_answer or '')
                if correct:
                    total_score += question.score
            elif question.question_type == 'blank':
                correct = False  # 填空题不再自动判分，交由教师评分
            elif question.question_type == 'short':
                correct = False  # 简答题不自动判分
            else:
                correct = user_answer == (question.correct_answer or '')
                if correct:
                    total_score += question.score
            is_correct_map[qid] = correct

        # 保存考试结果，包含学生答案和正误信息
        student_answers["_is_correct"] = is_correct_map
        result = ExamResult(
            user_id=current_user.id,
            exam_id=exam_id,
            score=total_score,
            total_possible_score=total_possible_score,
            answer_json=json.dumps(student_answers, ensure_ascii=False)
        )
        db.session.add(result)
        db.session.commit()
        update_learning_progress(current_user.id, exam.course_id)
        flash('考试已提交，感谢参与！')
        return redirect(url_for('exam_result', result_id=result.id))
    return render_template('take_exam.html', exam=exam, questions=questions)

#进度更新逻辑
def update_learning_progress(user_id, course_id):
    progress = LearningProgress.query.filter_by(user_id=user_id, course_id=course_id).first()
    if not progress:
        progress = LearningProgress(user_id=user_id, course_id=course_id)
        db.session.add(progress)

    # 保持视频进度不变
    video_percent = progress.video_watched_percentage or 0

    exams = Exam.query.filter_by(course_id=course_id).all()
    exam_ids = [exam.id for exam in exams]
    exam_results = ExamResult.query.filter(
        ExamResult.user_id == user_id,
        ExamResult.exam_id.in_(exam_ids)
    ).all()
    finished_exam_ids = {result.exam_id for result in exam_results}

    exam_count = len(exams)
    exam_completion_score = sum(1 for exam in exams if exam.id in finished_exam_ids)
    exam_percent = (exam_completion_score / exam_count) * 100 if exam_count else 0

    progress.exam_completed = (exam_completion_score == exam_count and exam_count > 0)
    progress.progress_percentage = (video_percent * 0.5) + (exam_percent * 0.5)
    progress.updated_at = datetime.datetime.utcnow()
    db.session.commit()


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

    # 获取试题
    questions = Question.query.filter_by(exam_id=exam.id).all()
    # 解析学生答案
    student_answers = {}
    is_correct_map = {}
    subjective_scores = {}
    if result.answer_json:
        try:
            student_answers = json.loads(result.answer_json)
            is_correct_map = student_answers.get("_is_correct", {})
            subjective_scores = student_answers.get("_subjective_scores", {})
        except Exception:
            student_answers = {}
            is_correct_map = {}
            subjective_scores = {}
    else:
        student_answers = {}
        is_correct_map = {}
        subjective_scores = {}

    # 处理答案显示格式
    def format_answer(ans, qtype):
        if ans is None:
            return ''
        if qtype == 'multiple':
            # 只显示非空选项，逗号分隔
            return ','.join([x for x in ans.split(',') if x.strip()])
        elif qtype == 'judge':
            if ans == 'A':
                return '正确'
            elif ans == 'B':
                return '错误'
            else:
                return ans
        else:
            return ans

    return render_template(
        'exam_result.html',
        result=result,
        exam=exam,
        questions=questions,
        total_score=result.total_possible_score,
        student_answers=student_answers,
        format_answer=format_answer,
        is_correct_map=is_correct_map,
        subjective_scores=subjective_scores
    )


# 学生学习进度
@app.route('/student/progress')
@login_required
def learning_progress():
    if current_user.role != 'student':
        abort(403)

    # 获取所有课程和学习进度
    courses = Course.query.all()
    # 获取当前用户所有进度记录
    progress_records = LearningProgress.query.filter_by(user_id=current_user.id).all()

    # 构建课程与进度的映射
    progress_map = {record.course_id: record for record in progress_records}

    # 构建每门课程的进度数据，若无记录则补全默认值
    progress = []
    for course in courses:
        record = progress_map.get(course.id)
        if record:
            video_watched_percentage = record.video_watched_percentage or 0
            exam_completed = record.exam_completed
            progress_percentage = record.progress_percentage or 0
            updated_at = record.updated_at
        else:
            # 没有进度记录，全部为0/未完成
            video_watched_percentage = 0
            exam_completed = False
            progress_percentage = 0
            updated_at = None
        progress.append({
            'course': course,
            'video_watched_percentage': video_watched_percentage,
            'exam_completed': exam_completed,
            'progress_percentage': progress_percentage,
            'updated_at': updated_at
        })

    return render_template('learning_progress.html', progress=progress)

@app.route('/student/courses')
@login_required
def student_courses():
    if current_user.role != 'student':
        abort(403)
    courses = current_user.enrolled_courses
    progress_records = LearningProgress.query.filter_by(user_id=current_user.id).all()
    progress_map = {r.course_id: r for r in progress_records}
    course_status = []
    for c in courses:
        progress = progress_map.get(c.id)
        percent = progress.progress_percentage if progress else 0
        course_status.append({
            'course': c,
            'progress_percentage': percent,
            'is_ended': c.is_ended
        })
    # 排序：未完成(0) < 已完成(1) < 已结束(2)
    def sort_key(item):
        if item['is_ended']:
            return 2
        elif item['progress_percentage'] >= 100:
            return 1
        else:
            return 0
    course_status = sorted(course_status, key=sort_key)
    return render_template('student_courses.html', course_status=course_status)
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

@app.route('/student/course/enroll/<int:course_id>', methods=['POST'])
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

@app.route('/teacher/student/<int:student_id>/progress')
@login_required
def teacher_view_student_progress(student_id):
    if current_user.role != 'teacher':
        abort(403)
    course_id = request.args.get('course_id', type=int)
    student = User.query.get_or_404(student_id)
    if not course_id:
        abort(400)
    course = Course.query.get_or_404(course_id)
    # 检查该学生是否属于该课程
    if student not in course.students:
        abort(403)
    progress = LearningProgress.query.filter_by(user_id=student_id, course_id=course_id).first()
    # 获取已完成考试ID
    exams = Exam.query.filter_by(course_id=course_id).all()
    exam_ids = [exam.id for exam in exams]
    exam_results = ExamResult.query.filter(
        ExamResult.user_id == student_id,
        ExamResult.exam_id.in_(exam_ids)
    ).all()
    finished_exam_ids = {result.exam_id for result in exam_results}
    return render_template(
        'student_learning_progress.html',
        student=student,
        course=course,
        progress=progress,
        finished_exam_ids=finished_exam_ids,
        video_watched_percentage=progress.video_watched_percentage if progress else 0,
        exam_completed=progress.exam_completed if progress else False,
        progress_percentage=progress.progress_percentage if progress else 0,
        updated_at=progress.updated_at if progress else None
    )


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

# 教师课程管理 - 删除课程
@app.route('/teacher/course/<int:course_id>/delete', methods=['POST'])
@login_required
def delete_course(course_id):
    if current_user.role != 'teacher':
        abort(403)
    course = Course.query.get_or_404(course_id)
    if course.teacher_id != current_user.id:
        abort(403)
    # 删除视频文件
    if course.video_filename:
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], course.video_filename)
        if os.path.exists(video_path):
            os.remove(video_path)
    # 删除学习材料
    if course.learning_materials:
        material_path = os.path.join(app.config['MATERIALS_FOLDER'], course.learning_materials)
        if os.path.exists(material_path):
            os.remove(material_path)
    # 删除相关考试、试题、成绩、进度等
    for exam in course.exams:
        for question in exam.questions:
            db.session.delete(question)
        for result in exam.exam_results:
            db.session.delete(result)
        db.session.delete(exam)
    # 删除学习进度
    from models import LearningProgress
    LearningProgress.query.filter_by(course_id=course.id).delete()
    # 移除学生选课关系
    # course.students.clear()
    for student in course.students.all():
        current_user.enrolled_courses.remove(course) if hasattr(current_user, 'enrolled_courses') and course in current_user.enrolled_courses else None
        student.enrolled_courses.remove(course)
    db.session.delete(course)
    try:
        db.session.commit()
        flash('课程已删除')
    except IntegrityError:
        db.session.rollback()
        flash('删除失败，存在关联数据')
    return redirect(url_for('teacher_dashboard'))

# 教师课程管理 - 结束课程
@app.route('/teacher/course/<int:course_id>/end', methods=['POST'])
@login_required
def end_course(course_id):
    if current_user.role != 'teacher':
        abort(403)
    course = Course.query.get_or_404(course_id)
    if course.teacher_id != current_user.id:
        abort(403)
    if not course.is_ended:
        course.is_ended = True
        db.session.commit()
        flash('课程已结束，学生将无法继续学习。')
    else:
        flash('课程已处于结束状态。')
    return redirect(url_for('edit_course', course_id=course.id))
# 多门考试结果查询
@app.route('/student/course/<int:course_id>/exam_select')
@login_required
@student_course_active_required
def student_exam_select(course_id):
    if current_user.role != 'student':
        abort(403)
    course = Course.query.get_or_404(course_id)
    exams = Exam.query.filter_by(course_id=course_id).all()
    # 获取当前学生所有考试结果
    results_map = {}
    exam_results = ExamResult.query.filter_by(user_id=current_user.id).all()
    for result in exam_results:
        results_map[result.exam_id] = result
    return render_template('student_exam_select.html', course=course, exams=exams, results_map=results_map)

# 学生选择考试参加
@app.route('/student/course/<int:course_id>/exam_take_select')
@login_required
@student_course_active_required
def student_exam_take_select(course_id):
    if current_user.role != 'student':
        abort(403)
    course = Course.query.get_or_404(course_id)
    exams = Exam.query.filter_by(course_id=course_id).all()
    # 获取已参加的考试
    taken_exam_ids = {r.exam_id for r in ExamResult.query.filter_by(user_id=current_user.id).all()}
    # 只显示未参加的考试
    available_exams = [exam for exam in exams if exam.id not in taken_exam_ids]
    return render_template('student_exam_take_select.html', course=course, exams=available_exams)

# 删除考试
@app.route('/teacher/exam/<int:exam_id>/delete', methods=['POST'])
@login_required
def delete_exam(exam_id):
    if current_user.role != 'teacher':
        abort(403)
    exam = Exam.query.get_or_404(exam_id)
    course = exam.course
    # 只能删除自己课程下的考试
    if course.teacher_id != current_user.id:
        abort(403)
    # 删除考试下所有试题和成绩
    for question in exam.questions:
        db.session.delete(question)
    for result in exam.exam_results:
        db.session.delete(result)
    db.session.delete(exam)
    db.session.commit()
    flash('考试已删除')
    return redirect(url_for('teacher_course_exams', course_id=course.id))

@app.route('/grade_subjective/<int:exam_id>', methods=['GET', 'POST'])
@login_required
def grade_subjective(exam_id):
    if current_user.role != 'teacher':
        abort(403)
    exam = Exam.query.get_or_404(exam_id)
    # 只允许本教师批改自己课程的考试
    if exam.course.teacher_id != current_user.id:
        abort(403)
    # 只筛选主观题（假设类型为'short'或'blank'为主观题，根据你的模型调整）
    questions = [q for q in exam.questions if q.question_type in ('short', 'blank')]
    results = ExamResult.query.filter_by(exam_id=exam_id).all()
    # 解析学生答案和主观题得分
    for result in results:
        try:
            result.parsed_answers = json.loads(result.answer_json) if result.answer_json else {}
        except Exception:
            result.parsed_answers = {}
        # 新增：解析每道主观题的得分
        result.subjective_scores = {}
        for q in questions:
            score_key = f"score_{q.id}"
            # 优先取answer_json中的分数
            if result.parsed_answers.get('_subjective_scores', {}).get(str(q.id)) is not None:
                result.subjective_scores[str(q.id)] = result.parsed_answers['_subjective_scores'][str(q.id)]
            else:
                result.subjective_scores[str(q.id)] = None
    if request.method == 'POST':
        for result in results:
            subjective_score = 0
            subjective_scores = {}
            for q in questions:
                score_key = f'score_{q.id}_{result.id}'
                score_val = request.form.get(score_key)
                if score_val:
                    try:
                        score = float(score_val)
                    except ValueError:
                        score = 0
                    subjective_score += score
                    subjective_scores[str(q.id)] = score
            result.subjective_score = subjective_score
            # 保存每道主观题分数到answer_json
            try:
                parsed = json.loads(result.answer_json) if result.answer_json else {}
            except Exception:
                parsed = {}
            parsed['_subjective_scores'] = subjective_scores
            result.answer_json = json.dumps(parsed, ensure_ascii=False)
            db.session.commit()
        # 重新判分所有相关学生答卷（会自动更新is_correct_map和总分）
        rejudge_exam_results(exam_id)
        flash('批改结果已保存', 'success')
        return redirect(url_for('grade_subjective', exam_id=exam_id))
    return render_template('grade_subjective.html', exam=exam, questions=questions, results=results)

    progress.progress_percentage = (progress.video_watched_percentage * 0.5) + (exam_percent * 0.5)
    progress.updated_at = datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


#更新学习进度
@app.route('/api/update_progress', methods=['POST'])
@login_required
def api_update_progress():
    data = request.get_json()
    course_id = data.get('course_id')
    video_percent = data.get('video_percent')
    if course_id is None or video_percent is None:
        return jsonify({'error': '参数缺失'}), 400

    progress = LearningProgress.query.filter_by(user_id=current_user.id, course_id=course_id).first()
    if not progress:
        progress = LearningProgress(user_id=current_user.id, course_id=course_id)
        db.session.add(progress)

    progress.video_watched_percentage = max(progress.video_watched_percentage or 0, video_percent)

    exams = Exam.query.filter_by(course_id=course_id).all()
    exam_ids = [exam.id for exam in exams]
    exam_results = ExamResult.query.filter(
        ExamResult.user_id == current_user.id,
        ExamResult.exam_id.in_(exam_ids)
    ).all()
    finished_exam_ids = {result.exam_id for result in exam_results}

    # 平均分配每个考试的进度
    exam_count = len(exams)
    exam_completion_score = sum(1 for exam in exams if exam.id in finished_exam_ids)
    if exam_count == 0:
        exam_percent = 100
        progress.exam_completed = True
    else:
        exam_percent = (exam_completion_score / exam_count) * 100
        progress.exam_completed = (exam_completion_score == exam_count)
    # 所有考试完成才标记为已完成
    progress.exam_completed = (exam_completion_score == exam_count and exam_count > 0)
    progress.progress_percentage = (progress.video_watched_percentage * 0.5) + (exam_percent * 0.5)
    progress.updated_at = datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


@app.route('/debug/progress/<int:user_id>/<int:course_id>')
def debug_progress(user_id, course_id):
    progress = LearningProgress.query.filter_by(user_id=user_id, course_id=course_id).first()
    if progress:
        return f"学习进度记录: {progress.video_watched_percentage}%"
    else:
        return "没有找到学习进度记录"



def rejudge_exam_results(exam_id):
    # 获取所有试题
    questions = Question.query.filter_by(exam_id=exam_id).all()
    question_map = {str(q.id): q for q in questions}
    # 获取所有答卷
    results = ExamResult.query.filter_by(exam_id=exam_id).all()
    for result in results:
        # 解析学生答案
        try:
            student_answers = json.loads(result.answer_json) if result.answer_json else {}
        except Exception:
            student_answers = {}
        total_score = 0
        total_possible_score = sum(q.score for q in questions)
        # 新增：为每道题保存正误信息
        is_correct_map = {}
        for question in questions:
            qid = str(question.id)
            user_answer = student_answers.get(qid, '')
            correct = False
            if question.question_type == 'multiple':
                user_answer = ','.join(sorted([ans.strip() for ans in user_answer.split(',') if ans.strip() in ['A', 'B', 'C', 'D']]))
                correct_answer = ','.join(sorted([ans.strip() for ans in (question.correct_answer or '').split(',') if ans.strip() in ['A', 'B', 'C', 'D']]))
                if user_answer == correct_answer:
                    total_score += question.score
                    correct = True
            elif question.question_type == 'judge':
                if user_answer == (question.correct_answer or ''):
                    total_score += question.score
                    correct = True
            elif question.question_type == 'blank':
                correct = False  # 填空题不再自动判分，交由教师评分
            elif question.question_type == 'short':
                correct = False  # 简答题不自动判分
            else:
                if user_answer == (question.correct_answer or ''):
                    total_score += question.score
                    correct = True
            is_correct_map[qid] = correct
        # 合并正误信息到answer_json
        if isinstance(student_answers, dict):
            student_answers["_is_correct"] = is_correct_map
            result.answer_json = json.dumps(student_answers, ensure_ascii=False)
        # 累加主观题分数
        subjective_score = result.subjective_score or 0
        result.score = total_score + subjective_score
        result.total_possible_score = total_possible_score
        db.session.commit()

# 学生证书页面
@app.route('/certificates')
@login_required
def certificates():
    if current_user.role == 'student':
        # 学生证书逻辑
        courses = current_user.enrolled_courses
        progress_records = LearningProgress.query.filter_by(user_id=current_user.id).all()
        progress_map = {r.course_id: r for r in progress_records}
        completed_courses = [
            c for c in courses
            if progress_map.get(c.id)
               and (progress_map[c.id].progress_percentage or 0) >= 100
               and c.is_ended
        ]
        completed_count = len(completed_courses)
        certificate_thresholds = [5, 10, 15, 20]
        certificates = []
        for threshold in certificate_thresholds:
            if completed_count >= threshold:
                certificates.append({
                    'level': threshold,
                    'name': f'完成{threshold}门课程证书'
                })
        return render_template(
            'certificates.html',
            role='student',
            certificates=certificates,
            completed_courses=completed_courses
        )
    elif current_user.role == 'teacher':
        courses = Course.query.filter_by(teacher_id=current_user.id).all()
        ended_courses = [c for c in courses if c.is_ended]
        ended_count = len(ended_courses)
        certificate_thresholds = [3, 6, 9, 12]
        certificates = []
        for threshold in certificate_thresholds:
            if ended_count >= threshold:
                certificates.append({
                    'level': threshold,
                    'name': f'授课{threshold}门课程证书'
                })
        return render_template(
            'certificates.html',
            role='teacher',
            certificates=certificates,
            ended_count=ended_count,
            ended_courses=ended_courses  # 新增
        )
    else:
        abort(403)
    # 这里根据你的业务逻辑获取已完成课程和证书
    courses = current_user.enrolled_courses
    progress_records = LearningProgress.query.filter_by(user_id=current_user.id).all()
    progress_map = {r.course_id: r for r in progress_records}
    completed_courses = [
        c for c in courses
        if progress_map.get(c.id)
           and (progress_map[c.id].progress_percentage or 0) >= 100
           and c.is_ended
    ]
    completed_count = len(completed_courses)
    certificate_thresholds = [5, 10, 15, 20]
    certificates = []
    for threshold in certificate_thresholds:
        if completed_count >= threshold:
            certificates.append({
                'level': threshold,
                'name': f'完成{threshold}门课程证书'
            })
    return render_template(
        'student_certificates.html',
        completed_courses=completed_courses,
        certificates=certificates
    )

@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, message='您无权限查看此页面。'), 403

@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', code=404, message='您访问的页面不存在。'), 404

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
