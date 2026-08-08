import os
import re

base_dir = r"c:\Users\L0Q\Desktop\security fast api project\securetrack_app\lib\screens"
lib_dir = r"c:\Users\L0Q\Desktop\security fast api project\securetrack_app\lib"

for root, dirs, files in os.walk(base_dir):
    for file in files:
        if file.endswith('.dart'):
            filepath = os.path.join(root, file)
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original_content = content
            
            # Remove showLogo
            content = re.sub(r'showLogo:\s*(true|false)\s*,?\s*', '', content)
            
            # Extract title if present in STAppBar
            # We look for STAppBar(... title: <expr>, ...)
            # Since dart expressions can contain parentheses (like t(ref, '...')), we match up to the end of the line usually, or manually match.
            
            lines = content.split('\n')
            new_lines = []
            title_expr = None
            in_app_bar = False
            
            for line in lines:
                if 'STAppBar(' in line:
                    in_app_bar = True
                
                if in_app_bar and 'title:' in line:
                    # Extract title
                    match = re.search(r'title:\s*(.*?),?$', line.rstrip())
                    if match:
                        title_expr = match.group(1).strip()
                        if title_expr.endswith(','):
                            title_expr = title_expr[:-1].strip()
                    # Skip adding this line to new_lines
                    continue
                
                if in_app_bar and ')' in line: # simplistic end of appbar
                    pass
                
                new_lines.append(line)
            
            content = '\n'.join(new_lines)
            
            # If we found a title, insert STScreenTitle and import
            if title_expr:
                # Add import
                rel_path = os.path.relpath(filepath, start=lib_dir)
                depth = len(rel_path.split(os.sep)) - 1
                prefix = '../' * depth
                import_stmt = f"import '{prefix}widgets/st_screen_title.dart';"
                
                if import_stmt not in content:
                    import_idx = content.find("import '")
                    if import_idx != -1:
                        content = content[:import_idx] + import_stmt + '\n' + content[import_idx:]
                
                # Insert STScreenTitle at first children: [ after body:
                body_idx = content.find('body:')
                if body_idx != -1:
                    children_idx = content.find('children: [', body_idx)
                    if children_idx != -1:
                        # Before we insert, check if the children array is inside a const widget
                        # If so, we might have an issue, but let's just insert it and we can fix compiler errors later.
                        insertion = f"children: [\n              STScreenTitle(title: {title_expr}),"
                        content = content[:children_idx] + insertion + content[children_idx + len('children: ['):]
                    else:
                        print(f"WARN: Could not find 'children: [' after 'body:' in {file}. Needs manual STScreenTitle insertion. Title: {title_expr}")
            
            if content != original_content:
                # Fix any `const STAppBar` that might now be empty and should just be `STAppBar` if we removed const fields? 
                # Actually, if we removed `showLogo: true` from `const STAppBar(...)`, it remains valid const.
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"Updated {file}")
