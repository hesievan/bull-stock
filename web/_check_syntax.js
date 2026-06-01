const fs = require('fs');
const html = fs.readFileSync('web/app.html', 'utf8');
const jsMatch = html.match(/<script>([\s\S]+?)<\/script>/);
if (!jsMatch) { console.log('no script found'); process.exit(1); }
try {
  new Function(jsMatch[1]);
  console.log('JS syntax OK, script length:', jsMatch[1].length);
} catch(e) {
  console.error('JS ERROR:', e.message);
}
