import re
src = open("client.py", encoding="utf-8").read()
checks = [
    ("_EMBEDDED_PUBKEY = .+", "_EMBEDDED_PUBKEY"),
    ("_AGENT_NAME = .+", "_AGENT_NAME"),
    ('SERVER_IP = os\\.environ\\.get\\("SERVER_IP", "[^"]*"\\)', "SERVER_IP"),
    ('SERVER_PORT = int\\(os\\.environ\\.get\\("SERVER_PORT", os\\.environ\\.get\\("PORT", "[^"]*"\\)\\)\\)', "SERVER_PORT"),
]
for pattern, name in checks:
    m = re.search(pattern, src)
    print(f"{name}: {'FOUND' if m else 'MISSING'}")
    if m:
        print(f"  -> {m.group()[:90]}")

print("\nkeys/ directory:", "EXISTS" if __import__("os").path.exists("keys") else "NOT FOUND")
