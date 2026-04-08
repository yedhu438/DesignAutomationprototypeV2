
content = open(r'W:\VarsaniAutomation\batch_processor.py', encoding='utf-8').read()

OLD = '''    # Gym / Swim
    ("GymLeo",                        "default"),
    ("SwimSuit",                      "default"),
]'''

NEW = '''    # Custom Tee variants (same canvas as standard tees)
    ("CustomKidsTee_",                "kidstshirt"),   # e.g. CustomKidsTee_Blk78
    ("Custom_Tee_",                   "adulttshirt"),  # e.g. Custom_Tee_BlkM
    # Gym / Swim
    ("GymLeo",                        "default"),
    ("SwimSuit",                      "default"),
]'''

if OLD in content:
    content = content.replace(OLD, NEW)
    open(r'W:\VarsaniAutomation\batch_processor.py', 'w', encoding='utf-8').write(content)
    print("OK: CustomKidsTee_ and Custom_Tee_ added to SKU_MAP")
else:
    print("ERROR: marker not found")
