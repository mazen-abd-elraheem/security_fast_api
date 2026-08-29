import re

with open(r'c:\Users\L0Q\Desktop\security fast api project\seed_db.py', 'r', encoding='utf-8') as f:
    content = f.read()

bad1 = r'        from sqlalchemy import inspect as sa_inspect, text as sa_text\n    insp = sa_inspect\(engine\)\n    if insp\.has_table\(\"attendance_logs\"\):'
bad2 = r'        from sqlalchemy import inspect as sa_inspect, text as sa_text\r\n    insp = sa_inspect\(engine\)\r\n    if insp\.has_table\(\"attendance_logs\"\):'

content = re.sub(bad1, '        if insp.has_table(\"attendance_logs\"):', content)
content = re.sub(bad2, '        if insp.has_table(\"attendance_logs\"):', content)

with open(r'c:\Users\L0Q\Desktop\security fast api project\seed_db.py', 'w', encoding='utf-8') as f:
    f.write(content)
