const fs = require('fs');
const acorn = require('acorn');

const s = fs.readFileSync('server.py','utf-8');
const i = s.indexOf('DASHBOARD');
const q = s.indexOf('"""', i);
const e = s.lastIndexOf('"""');
const h = s.substring(q+3, e);
const j = h.substring(h.indexOf('<script>')+8, h.indexOf('</script>'));

try {
    acorn.parse(j, { ecmaVersion: 2022, sourceType: 'script' });
    console.log('JS PARSED OK');
} catch(ex) {
    console.log('PARSE ERROR:', ex.message);
    if (ex.loc) {
        console.log(`  at line ${ex.loc.line}, column ${ex.loc.column}`);
        const lines = j.split('\n');
        const errLine = lines[ex.loc.line - 1];
        if (errLine) console.log(`  content: ${errLine.substring(0, 120)}`);
    }
}
