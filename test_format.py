import py_compile
py_compile.compile("remote_manager.py", doraise=True)

# Simulate the format call
src = open("remote_manager.py", encoding="utf-8").read()

class FakeManager:
    config = {"pdf_path": "test.pdf", "use_pdf": True}
    def _gen_key(self):
        import random
        return bytes(random.randrange(256) for _ in range(32))

m = FakeManager()

# Extract the template
idx = src.find("ENCRYPTED_LOADER_TEMPLATE")
start = src.index("'''", idx) + 3
end = src.index("'''", start)
template = src[start:end]

# Try formatting
try:
    result = template.format(pdf_name="decoy.pdf", enc_key="aabb", enc_data="[1,2,3]")
    # Verify the result compiles as valid Python
    compile(result, "<loader>", "exec")
    print("Template format + compile: OK")
except Exception as e:
    print(f"ERROR: {e}")
