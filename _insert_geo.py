"""Script to fix GeoIP stealth re-initialization placement."""
import sys

path = r'c:\Desktop\auto_reg-main\auto_reg-main\platforms\chatgpt\refresh_token_registration_engine.py'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Remove the wrongly inserted block first
wrong_block = """
            # 根据 GeoIP 重新初始化隐蔽性组件（使 Accept-Language 匹配代理 IP 地区）
            if location:
                self._reinit_stealth_components(geo_code=location)
                self._log(f"Session 指纹已初始化: {self._session_fp}")

"""
if wrong_block in content:
    content = content.replace(wrong_block, '', 1)
    print("Removed wrongly placed block")

# Now find the correct location: the line with just "IP 位" (the success log, not the error one)
# We look for the pattern: self._log(f"IP 位置: {location}")  followed by blank line and then "# 2."
lines = content.split('\n')

target_idx = None
for i, line in enumerate(lines):
    # Match the success log line (contains "位置" but NOT "失败")
    if '_log' in line and 'location' in line and i > 2700 and i < 2800:
        # Check it's NOT the error line
        stripped = line.strip()
        if 'error' not in stripped and 'return' not in stripped:
            target_idx = i
            print(f"  Candidate line {i+1}: {line.rstrip()}")

if target_idx is None:
    print("ERROR: Could not find target line")
    sys.exit(1)

print(f"Using target line {target_idx + 1}")

# Find the next comment line starting with "#"
insert_idx = target_idx + 1
while insert_idx < len(lines) and lines[insert_idx].strip() == '':
    insert_idx += 1

print(f"Inserting before line {insert_idx + 1}: {lines[insert_idx].rstrip()}")

new_lines = [
    '',
    '            # 根据 GeoIP 重新初始化隐蔽性组件（使 Accept-Language 匹配代理 IP 地区）',
    '            if location:',
    '                self._reinit_stealth_components(geo_code=location)',
    '                self._log(f"Session 指纹已初始化: {self._session_fp}")',
    '',
]

lines = lines[:insert_idx] + new_lines + lines[insert_idx:]

with open(path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print(f"Successfully inserted {len(new_lines)} lines at position {insert_idx + 1}")
