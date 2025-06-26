from db import db
import datetime
from flask_login import UserMixin

# 添加学生选课关系表
student_course = db.Table('student_course',
    db.Column('student_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('course_id', db.Integer, db.ForeignKey('course.id'))
)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='student')  # teacher/student角色
    is_active = db.Column(db.Boolean, default=True)  # 用户活跃状态
    courses = db.relationship('Course', backref='teacher', lazy=True)
    forum_posts = db.relationship('ForumPost', backref='user', lazy=True)
    forum_replies = db.relationship('ForumReply', backref='user', lazy=True)
    exam_results = db.relationship('ExamResult', backref='user_exam_result', lazy=True)
    # 添加学生选课关系
    enrolled_courses = db.relationship('Course', secondary=student_course, backref=db.backref('students', lazy='dynamic'))

class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    video_filename = db.Column(db.String(100), default='')  # 视频文件名
    upload_date = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    exams = db.relationship('Exam', backref='course', lazy=True)
    learning_materials = db.Column(db.String(200))  # 学习材料文件名

    def get_average_score(self):
        results = ExamResult.query.join(Exam).filter(Exam.course_id == self.id).all()
        if not results:
            return 0
        total_score = sum([result.score for result in results])
        return total_score / len(results)

class Exam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    duration = db.Column(db.Integer, default=60)  # 考试时长（分钟）
    questions = db.relationship('Question', backref='exam', lazy=True)
    exam_results = db.relationship('ExamResult', backref='exam_of_result', lazy=True)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('exam.id'), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    options = db.Column(db.JSON, nullable=True)  # 选择题/判断题有选项
    correct_answer = db.Column(db.Text, nullable=False)  # 支持多选/填空/简答/判断
    score = db.Column(db.Integer, default=1)
    explanation = db.Column(db.Text)
    question_type = db.Column(db.String(20), default='single')  # 题型: single/multiple/blank/short/judge
    image = db.Column(db.String(200))  # 图片材料路径


class ExamResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey('exam.id'), nullable=False)
    score = db.Column(db.Float, nullable=False)
    total_possible_score = db.Column(db.Float, nullable=False)
    submission_time = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    answer_json = db.Column(db.Text)  # 新增：存储学生作答

    # 关系
    user = db.relationship('User', backref='user_exam_result', lazy=True, overlaps="exam_results,user_exam_result")
    exam = db.relationship('Exam', backref='results', lazy=True, overlaps="exam_of_result,exam_results")

class LearningProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('learning_progress', lazy=True))
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    course = db.relationship('Course', backref=db.backref('learning_progress', lazy=True))
    video_watched_percentage = db.Column(db.Float, default=0)  # 视频观看百分比
    exam_completed = db.Column(db.Boolean, default=False)  # 考试是否完成
    progress_percentage = db.Column(db.Float, default=0)  # 综合学习进度百分比
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class ForumPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    replies = db.relationship('ForumReply', backref='post', lazy=True)

class ForumReply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('forum_post.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)