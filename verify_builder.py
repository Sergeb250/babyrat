import py_compile

py_compile.compile("builder.py", doraise=True)
print("builder.py: compiles OK")

src = open("builder.py", encoding="utf-8").read()

# Test LOADER_TEMPLATE
start = src.find("LOADER_TEMPLATE = ") + len("LOADER_TEMPLATE = ")
start = src.index('"""', start) + 3
end = src.index('"""', start)
template = src[start:end]
result = template.format(pdf_name="test.pdf", enc_key="aa", enc_data=repr([1, 2, 3]))
compile(result, "<loader>", "exec")
print("LOADER_TEMPLATE: formats and compiles OK")

# Test SPEC_TEMPLATE
start2 = src.find("SPEC_TEMPLATE = ") + len("SPEC_TEMPLATE = ")
start2 = src.index('"""', start2) + 3
end2 = src.index('"""', start2)
t2 = src[start2:end2]
t2.format(
    client_file="x", datas="[]", output_name="x",
    upx="False", console="False", onefile="True",
    uac_line="", icon_line="",
)
print("SPEC_TEMPLATE: formats OK")

# Verify all named placeholders are provided
import re
placeholders = set(re.findall(r"\{(\w+)\}", template))
provided = {"pdf_name", "enc_key", "enc_data"}
missing = placeholders - provided
if missing:
    print(f"LOADER_TEMPLATE missing placeholders: {missing}")
else:
    print(f"LOADER_TEMPLATE placeholders: {placeholders}")

placeholders2 = set(re.findall(r"\{(\w+)\}", t2))
provided2 = {"client_file", "datas", "output_name", "upx", "console", "onefile", "uac_line", "icon_line"}
missing2 = placeholders2 - provided2
if missing2:
    print(f"SPEC_TEMPLATE missing placeholders: {missing2}")
else:
    print(f"SPEC_TEMPLATE placeholders: {placeholders2}")

print("ALL CHECKS PASSED")
