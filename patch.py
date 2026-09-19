with open("fb_pipeline/contracts/l1_class_schedule.py", "r") as f:
    content = f.read()

old = """
    for section in sections:
        heading = section["heading"].lower()
        if place_key in heading and (time_key in heading or f"{definition.hour}h" in heading):
"""
new = """
    for section in sections:
        heading = section["heading"].lower()
        body = section["body"].lower()
        # time might be formatted as 14:30 in the heading
        time_colon = f"{definition.hour}:{definition.minute:02d}"
        time_match = time_key in heading or f"{definition.hour}h" in heading or time_colon in heading
        
        if (place_key in heading or place_key in body) and time_match:
"""

if old in content:
    with open("fb_pipeline/contracts/l1_class_schedule.py", "w") as f:
        f.write(content.replace(old, new))
    print("Patched!")
else:
    print("Old content not found.")
