import py_compile
py_compile.compile("remote_manager.py", doraise=True)
py_compile.compile("client.py", doraise=True)

src = open("remote_manager.py", encoding="utf-8").read()
idx = src.find("ENCRYPTED_LOADER_TEMPLATE")
start = src.index("'''", idx) + 3
end = src.index("'''", start)
template = src[start:end]

braces_open = template.count("{")
braces_close = template.count("}")
print(f"Template braces: {braces_open} open, {braces_close} close")
if braces_open == braces_close:
    print("Template brace balance: OK")
else:
    print("Template brace balance: MISMATCH!")

named = []
for i, c in enumerate(template):
    if c == "{":
        j = i
        while j < len(template) and template[j] != "}":
            j += 1
        if j < len(template):
            placeholder = template[i:j+1]
            if placeholder not in ("{{", "}}"):
                named.append(placeholder)

print(f"Named placeholders: {named}")
print("ALL OK" if braces_open == braces_close else "ISSUES FOUND")
