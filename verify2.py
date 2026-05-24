import py_compile
py_compile.compile("builder.py", doraise=True)
print("builder.py: compiles OK")

src = open("builder.py", encoding="utf-8").read()

# Test LOADER_TEMPLATE format
start = src.find("LOADER_TEMPLATE = ") + len("LOADER_TEMPLATE = ")
start = src.index('"""', start) + 3
end = src.index('"""', start)
template = src[start:end]
result = template.format(pdf_name="test.pdf", enc_key="aa", enc_data=repr([1, 2, 3]))
compile(result, "<loader>", "exec")
print("LOADER_TEMPLATE: formats + compiles OK")
print("Length:", len(result), "chars")
print("ALL GOOD")
