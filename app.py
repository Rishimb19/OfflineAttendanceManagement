# app.py
import sqlite3
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, g
)
from werkzeug.security import generate_password_hash, check_password_hash

# ------------------------------
# App Initialization
# ------------------------------
app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'

DATABASE = 'database.db'

# ------------------------------
# Database helpers
# ------------------------------
def get_db():
    """Get a database connection, stored in Flask's g object."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    """Close the database connection at the end of the request."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """Create tables and default admin if they don't exist."""
    with app.app_context():
        db = get_db()
        cursor = db.cursor()

        # Students table with USN, phone, class, department
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usn TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                phone TEXT,
                class TEXT NOT NULL,
                department TEXT NOT NULL
            )
        ''')

        # Attendance table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                status TEXT CHECK(status IN ('Present','Absent')) NOT NULL,
                FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                UNIQUE(student_id, date)
            )
        ''')

        # Tasks table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                due_date TEXT NOT NULL
            )
        ''')

        # Student-Task completion table (many-to-many)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS student_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                status TEXT CHECK(status IN ('Pending','Completed')) NOT NULL DEFAULT 'Pending',
                completed_date TEXT,
                FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE,
                FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                UNIQUE(task_id, student_id)
            )
        ''')

        # Admin table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        ''')

        # Insert default admin if none exists (username: admin, password: admin)
        cursor.execute("SELECT COUNT(*) FROM admin")
        if cursor.fetchone()[0] == 0:
            hashed = generate_password_hash('admin')
            cursor.execute("INSERT INTO admin (username, password_hash) VALUES (?, ?)",
                           ('admin', hashed))

        db.commit()

# ------------------------------
# Login required decorator
# ------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ------------------------------
# Helper: get distinct classes/departments
# ------------------------------
def get_class_department_options():
    db = get_db()
    classes = db.execute("SELECT DISTINCT class FROM students ORDER BY class").fetchall()
    departments = db.execute("SELECT DISTINCT department FROM students ORDER BY department").fetchall()
    return [c['class'] for c in classes], [d['department'] for d in departments]

# ------------------------------
# Routes
# ------------------------------
@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Admin login."""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        admin = db.execute(
            "SELECT * FROM admin WHERE username = ?", (username,)
        ).fetchone()
        if admin and check_password_hash(admin['password_hash'], password):
            session['admin_id'] = admin['id']
            session['admin_username'] = admin['username']
            flash('Login successful.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout admin."""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    """Dashboard with summary cards."""
    db = get_db()
    # Total students
    total_students = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    # Total attendance records
    total_attendance = db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
    # Present count
    present_count = db.execute(
        "SELECT COUNT(*) FROM attendance WHERE status = 'Present'"
    ).fetchone()[0]
    # Absent count
    absent_count = total_attendance - present_count
    # Task stats
    total_tasks = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    completed_tasks = db.execute(
        "SELECT COUNT(*) FROM student_tasks WHERE status = 'Completed'"
    ).fetchone()[0]

    # Recent attendance (last 5)
    recent = db.execute('''
        SELECT s.name, s.class, s.department, a.date, a.status
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        ORDER BY a.date DESC, a.id DESC
        LIMIT 5
    ''').fetchall()

    return render_template('dashboard.html',
                           total_students=total_students,
                           total_attendance=total_attendance,
                           present_count=present_count,
                           absent_count=absent_count,
                           total_tasks=total_tasks,
                           completed_tasks=completed_tasks,
                           recent=recent)

# ------------------------------
# Student Management
# ------------------------------
@app.route('/students')
@login_required
def students():
    """List all students."""
    db = get_db()
    students_list = db.execute('''
        SELECT * FROM students ORDER BY class, department, name
    ''').fetchall()
    classes, departments = get_class_department_options()
    return render_template('students.html', students=students_list,
                           classes=classes, departments=departments)

@app.route('/students/add', methods=['POST'])
@login_required
def add_student():
    """Add a new student."""
    usn = request.form['usn'].strip()
    name = request.form['name'].strip()
    email = request.form['email'].strip()
    phone = request.form['phone'].strip()
    class_ = request.form['class'].strip()
    department = request.form['department'].strip()

    if not usn or not name or not email or not class_ or not department:
        flash('USN, Name, Email, Class, and Department are required.', 'danger')
        return redirect(url_for('students'))

    db = get_db()
    try:
        db.execute(
            '''INSERT INTO students (usn, name, email, phone, class, department)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (usn, name, email, phone, class_, department)
        )
        db.commit()
        flash('Student added successfully.', 'success')
    except sqlite3.IntegrityError as e:
        if 'UNIQUE constraint failed' in str(e):
            flash('USN or Email already exists.', 'danger')
        else:
            flash('Error adding student.', 'danger')
    return redirect(url_for('students'))

@app.route('/students/edit/<int:id>', methods=['POST'])
@login_required
def edit_student(id):
    """Edit an existing student."""
    usn = request.form['usn'].strip()
    name = request.form['name'].strip()
    email = request.form['email'].strip()
    phone = request.form['phone'].strip()
    class_ = request.form['class'].strip()
    department = request.form['department'].strip()

    if not usn or not name or not email or not class_ or not department:
        flash('All fields except phone are required.', 'danger')
        return redirect(url_for('students'))

    db = get_db()
    # Check if USN/email already used by another student
    existing = db.execute(
        "SELECT id FROM students WHERE (usn = ? OR email = ?) AND id != ?",
        (usn, email, id)
    ).fetchone()
    if existing:
        flash('USN or Email already in use by another student.', 'danger')
        return redirect(url_for('students'))

    db.execute(
        '''UPDATE students SET usn=?, name=?, email=?, phone=?, class=?, department=?
           WHERE id=?''',
        (usn, name, email, phone, class_, department, id)
    )
    db.commit()
    flash('Student updated successfully.', 'success')
    return redirect(url_for('students'))

@app.route('/students/delete/<int:id>', methods=['POST'])
@login_required
def delete_student(id):
    """Delete a student (cascade deletes attendance and task records)."""
    db = get_db()
    db.execute("DELETE FROM students WHERE id = ?", (id,))
    db.commit()
    flash('Student deleted successfully.', 'success')
    return redirect(url_for('students'))

# ------------------------------
# Attendance (individual and bulk)
# ------------------------------
@app.route('/attendance', methods=['GET', 'POST'])
@login_required
def attendance():
    """Mark individual attendance and view history."""
    db = get_db()
    if request.method == 'POST':
        student_id = request.form['student_id']
        status = request.form['status']
        date = request.form.get('date', datetime.today().strftime('%Y-%m-%d'))
        try:
            db.execute(
                "INSERT INTO attendance (student_id, date, status) VALUES (?, ?, ?)",
                (student_id, date, status)
            )
            db.commit()
            flash('Attendance marked successfully.', 'success')
        except sqlite3.IntegrityError:
            flash('Attendance for this student on this date already exists.', 'danger')
        return redirect(url_for('attendance'))

    # GET: show form and history
    students_list = db.execute(
        "SELECT id, usn, name, class, department FROM students ORDER BY class, department, name"
    ).fetchall()
    history = db.execute('''
        SELECT s.name, s.class, s.department, a.date, a.status
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        ORDER BY a.date DESC, a.id DESC
    ''').fetchall()
    classes, departments = get_class_department_options()
    return render_template('attendance.html',
                           students=students_list,
                           history=history,
                           today=datetime.today().strftime('%Y-%m-%d'),
                           classes=classes,
                           departments=departments)

@app.route('/attendance/bulk', methods=['GET', 'POST'])
@login_required
def bulk_attendance():
    """Mark attendance for multiple students at once with filters."""
    db = get_db()
    if request.method == 'POST':
        date = request.form['date']
        class_filter = request.form.get('class_filter')
        dept_filter = request.form.get('dept_filter')
        query = "SELECT id FROM students"
        params = []
        conditions = []
        if class_filter and class_filter != 'All':
            conditions.append("class = ?")
            params.append(class_filter)
        if dept_filter and dept_filter != 'All':
            conditions.append("department = ?")
            params.append(dept_filter)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        student_ids = [row['id'] for row in db.execute(query, params).fetchall()]

        success_count = 0
        exists_count = 0
        for sid in student_ids:
            status = request.form.get(f'status_{sid}', 'Present')
            try:
                db.execute(
                    "INSERT INTO attendance (student_id, date, status) VALUES (?, ?, ?)",
                    (sid, date, status)
                )
                success_count += 1
            except sqlite3.IntegrityError:
                exists_count += 1
        db.commit()
        flash(f'Bulk attendance marked: {success_count} new records, {exists_count} already existed.', 'success')
        return redirect(url_for('attendance'))

    # GET: show form with filters
    classes, departments = get_class_department_options()
    class_filter = request.args.get('class_filter', 'All')
    dept_filter = request.args.get('dept_filter', 'All')
    query = "SELECT id, usn, name, class, department FROM students"
    params = []
    conditions = []
    if class_filter and class_filter != 'All':
        conditions.append("class = ?")
        params.append(class_filter)
    if dept_filter and dept_filter != 'All':
        conditions.append("department = ?")
        params.append(dept_filter)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY class, department, name"
    students = db.execute(query, params).fetchall()
    return render_template('bulk_attendance.html',
                           students=students,
                           classes=classes,
                           departments=departments,
                           selected_class=class_filter,
                           selected_dept=dept_filter,
                           today=datetime.today().strftime('%Y-%m-%d'))

# ------------------------------
# Tasks Management
# ------------------------------
@app.route('/tasks', methods=['GET', 'POST'])
@login_required
def tasks():
    """List tasks and add new ones."""
    db = get_db()
    if request.method == 'POST':
        title = request.form['title'].strip()
        description = request.form['description'].strip()
        due_date = request.form['due_date']
        if not title or not due_date:
            flash('Title and due date are required.', 'danger')
            return redirect(url_for('tasks'))

        cursor = db.execute(
            "INSERT INTO tasks (title, description, due_date) VALUES (?, ?, ?)",
            (title, description, due_date)
        )
        task_id = cursor.lastrowid

        assign_all = request.form.get('assign_all')
        if assign_all:
            students = db.execute("SELECT id FROM students").fetchall()
            for s in students:
                try:
                    db.execute(
                        "INSERT INTO student_tasks (task_id, student_id, status) VALUES (?, ?, 'Pending')",
                        (task_id, s['id'])
                    )
                except sqlite3.IntegrityError:
                    pass
            db.commit()
            flash('Task added and assigned to all students.', 'success')
        else:
            flash('Task added. Use "Assign" to assign to students.', 'success')
        return redirect(url_for('tasks'))

    tasks_list = db.execute('''
        SELECT t.*,
               COUNT(st.id) as total_assigned,
               SUM(CASE WHEN st.status = 'Completed' THEN 1 ELSE 0 END) as completed_count
        FROM tasks t
        LEFT JOIN student_tasks st ON t.id = st.task_id
        GROUP BY t.id
        ORDER BY t.due_date
    ''').fetchall()
    return render_template('tasks.html', tasks=tasks_list)

@app.route('/tasks/<int:task_id>')
@login_required
def task_detail(task_id):
    """Show all students and their completion status for a specific task."""
    db = get_db()
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        flash('Task not found.', 'danger')
        return redirect(url_for('tasks'))

    student_tasks = db.execute('''
        SELECT s.id as student_id, s.usn, s.name, s.class, s.department,
               st.status, st.completed_date
        FROM students s
        LEFT JOIN student_tasks st ON s.id = st.student_id AND st.task_id = ?
        ORDER BY s.class, s.department, s.name
    ''', (task_id,)).fetchall()

    return render_template('task_detail.html', task=task, student_tasks=student_tasks)

@app.route('/tasks/assign/<int:task_id>', methods=['GET', 'POST'])
@login_required
def assign_task(task_id):
    """Assign task to selected students (only those not already assigned)."""
    db = get_db()
    if request.method == 'POST':
        student_ids = request.form.getlist('student_ids')
        for sid in student_ids:
            try:
                db.execute(
                    "INSERT INTO student_tasks (task_id, student_id, status) VALUES (?, ?, 'Pending')",
                    (task_id, sid)
                )
            except sqlite3.IntegrityError:
                pass
        db.commit()
        flash('Task assigned to selected students.', 'success')
        return redirect(url_for('tasks'))

    # GET: list students not yet assigned
    assigned = db.execute(
        "SELECT student_id FROM student_tasks WHERE task_id = ?", (task_id,)
    ).fetchall()
    assigned_ids = [a['student_id'] for a in assigned]
    if assigned_ids:
        placeholders = ','.join('?' * len(assigned_ids))
        query = f"SELECT * FROM students WHERE id NOT IN ({placeholders}) ORDER BY class, department, name"
        students = db.execute(query, assigned_ids).fetchall()
    else:
        students = db.execute("SELECT * FROM students ORDER BY class, department, name").fetchall()
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return render_template('assign_task.html', task=task, students=students)

@app.route('/tasks/complete/<int:task_id>/<int:student_id>', methods=['POST'])
@login_required
def complete_task(task_id, student_id):
    """Mark a single student's task as completed."""
    db = get_db()
    today = datetime.today().strftime('%Y-%m-%d')
    db.execute(
        '''UPDATE student_tasks SET status='Completed', completed_date=?
           WHERE task_id=? AND student_id=?''',
        (today, task_id, student_id)
    )
    db.commit()
    flash('Task marked as completed.', 'success')
    return redirect(url_for('task_detail', task_id=task_id))

@app.route('/tasks/reset/<int:task_id>/<int:student_id>', methods=['POST'])
@login_required
def reset_task(task_id, student_id):
    """Reset a student's task to pending."""
    db = get_db()
    db.execute(
        '''UPDATE student_tasks SET status='Pending', completed_date=NULL
           WHERE task_id=? AND student_id=?''',
        (task_id, student_id)
    )
    db.commit()
    flash('Task status reset.', 'success')
    return redirect(url_for('task_detail', task_id=task_id))

@app.route('/tasks/bulk_complete/<int:task_id>', methods=['POST'])
@login_required
def bulk_complete_task(task_id):
    """Mark all pending students as completed for this task."""
    db = get_db()
    today = datetime.today().strftime('%Y-%m-%d')
    db.execute('''
        UPDATE student_tasks
        SET status = 'Completed', completed_date = ?
        WHERE task_id = ? AND status = 'Pending'
    ''', (today, task_id))
    db.commit()
    flash('All pending students marked as completed.', 'success')
    return redirect(url_for('task_detail', task_id=task_id))

@app.route('/tasks/assign_to_student/<int:task_id>/<int:student_id>', methods=['POST'])
@login_required
def assign_task_to_student(task_id, student_id):
    """Assign a task to an individual student (if not already assigned)."""
    db = get_db()
    try:
        db.execute(
            "INSERT INTO student_tasks (task_id, student_id, status) VALUES (?, ?, 'Pending')",
            (task_id, student_id)
        )
        db.commit()
        flash('Task assigned to student.', 'success')
    except sqlite3.IntegrityError:
        flash('Task already assigned to this student.', 'warning')
    return redirect(url_for('task_detail', task_id=task_id))

@app.route('/tasks/delete/<int:id>', methods=['POST'])
@login_required
def delete_task(id):
    """Delete a task."""
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id = ?", (id,))
    db.commit()
    flash('Task deleted.', 'success')
    return redirect(url_for('tasks'))

# ------------------------------
# Reports
# ------------------------------
@app.route('/reports')
@login_required
def reports():
    """Attendance reports per student with class/department filters."""
    db = get_db()
    class_filter = request.args.get('class_filter', 'All')
    dept_filter = request.args.get('dept_filter', 'All')

    query = '''
        SELECT s.id, s.name, s.class, s.department,
               COUNT(a.id) as total_days,
               SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) as present_count,
               SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) as absent_count
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id
    '''
    params = []
    conditions = []
    if class_filter and class_filter != 'All':
        conditions.append("s.class = ?")
        params.append(class_filter)
    if dept_filter and dept_filter != 'All':
        conditions.append("s.department = ?")
        params.append(dept_filter)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " GROUP BY s.id ORDER BY s.class, s.department, s.name"

    students_data = db.execute(query, params).fetchall()

    report_rows = []
    for row in students_data:
        total = row['total_days'] or 0
        present = row['present_count'] or 0
        percent = round((present / total * 100), 2) if total > 0 else 0
        report_rows.append({
            'name': row['name'],
            'class': row['class'],
            'department': row['department'],
            'total': total,
            'present': present,
            'absent': row['absent_count'] or 0,
            'percent': percent
        })

    classes, departments = get_class_department_options()
    return render_template('reports.html',
                           report=report_rows,
                           classes=classes,
                           departments=departments,
                           selected_class=class_filter,
                           selected_dept=dept_filter)

# ------------------------------
# Run the app
# ------------------------------
if __name__ == '__main__':
    init_db()
    app.run(debug=True)