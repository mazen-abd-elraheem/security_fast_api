from app.core.database import SessionLocal
from app.models.task_models import TaskInstance
from app.models.user import User

db = SessionLocal()
instances = db.query(TaskInstance).all()
print(f"Total instances: {len(instances)}")
for instance in instances:
    print(f"Instance ID: {instance.instance_id}, Assigned To: {instance.assigned_to}, Status: {instance.status}")
    user = db.query(User).filter_by(user_id=instance.assigned_to).first()
    if user:
        print(f"  -> Assigned To User: {user.name} ({user.role})")
    else:
        print("  -> Assigned to user not found!")
