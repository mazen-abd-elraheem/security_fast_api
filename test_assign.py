from app.core.database import SessionLocal
from app.models.task_models import TaskTemplate, TaskInstance, TaskInstanceStatus
from app.models.user import User
import uuid
import datetime

db = SessionLocal()
template = db.query(TaskTemplate).first()
user = db.query(User).filter_by(role="leader").first()
admin = db.query(User).filter_by(role="admin").first()

if template and user and admin:
    print(f"Testing assignment... Template: {template.template_id}, User: {user.user_id}, Admin: {admin.user_id}")
    instance = TaskInstance(
        instance_id=str(uuid.uuid4()),
        template_id=template.template_id,
        assigned_to=user.user_id,
        site_id=template.site_id,
        status=TaskInstanceStatus.PENDING,
        due_date=datetime.datetime.now(datetime.timezone.utc),
        created_by=admin.user_id,
    )
    db.add(instance)
    try:
        db.commit()
        print("Success! Instance created:", instance.instance_id)
    except Exception as e:
        print("Error:", e)
else:
    print(f"Missing data. Template: {template}, User: {user}, Admin: {admin}")
