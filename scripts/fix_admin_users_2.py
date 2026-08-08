import os
import re

filepath = r"c:\Users\L0Q\Desktop\security fast api project\securetrack_app\lib\screens\admin\admin_users_screen.dart"

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the missing `),` after `]` for the title Row.
# The pattern is:
#             ],
#           content: Column(
content = re.sub(
    r'\],\s*content:\s*Column\(',
    r'],\n          ),\n          content: Column(',
    content
)

# And if there are any remaining `backgroundColor: STColors.surfaceContainerLowest,\s*children: \[`, fix them too:
content = re.sub(
    r'backgroundColor:\s*STColors\.surfaceContainerLowest,\s*children:\s*\[',
    r'backgroundColor: STColors.surfaceContainerLowest,\n          title: Row(\n            children: [',
    content
)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
print("Fixed admin_users_screen.dart")
