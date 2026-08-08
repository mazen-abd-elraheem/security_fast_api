import os

filepath = r"c:\Users\L0Q\Desktop\security fast api project\securetrack_app\lib\screens\admin\admin_users_screen.dart"

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern 1: Missing title in dialog
broken_pattern = '''          backgroundColor: STColors.surfaceContainerLowest,
            children: ['''
fixed_pattern = '''          backgroundColor: STColors.surfaceContainerLowest,
          title: Row(
            children: ['''

content = content.replace(broken_pattern, fixed_pattern)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
print("Fixed admin_users_screen.dart dialogs")
