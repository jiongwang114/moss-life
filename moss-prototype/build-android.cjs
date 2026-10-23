/* Build the shared UI for Android 8 / Chrome 61. Never hand-copy a stale bundle. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const esbuild = require('esbuild');
const acorn = require('acorn');
const rewriteRegex = require('regexpu-core');
const target = ['es2017','chrome61'];
const root = __dirname;
const output = path.resolve(process.argv[2] || path.join(root, 'android-compat'));
if (output === root) throw Error('Output must not overwrite frontend source');
fs.mkdirSync(output, {recursive:true});
const assets = JSON.parse(fs.readFileSync(path.join(root,'assets.json'),'utf8'));
const hashes = {};
function lowerUnicodeRegex(source) {
  const edits = [];
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'Literal' && node.regex && /\\[pP]\{/.test(node.regex.pattern)) {
      const pattern = rewriteRegex(node.regex.pattern,node.regex.flags,{unicodePropertyEscapes:'transform'});
      edits.push([node.start,node.end,'new RegExp('+JSON.stringify(pattern)+','+JSON.stringify(node.regex.flags)+')']);
    } else if (node.type === 'Literal' && node.value === '\\p{P}\\p{S}') {
      // Marked builds delimiter expressions dynamically from this class body.
      const pattern = rewriteRegex('['+node.value+']','u',{unicodePropertyEscapes:'transform'});
      if (pattern[0] !== '[' || pattern[pattern.length-1] !== ']') throw Error('Unexpected Unicode punctuation class');
      edits.push([node.start,node.end,JSON.stringify(pattern.slice(1,-1))]);
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  }
  visit(acorn.parse(source,{ecmaVersion:'latest'}));
  for (const [start,end,replacement] of edits.sort((a,b)=>b[0]-a[0])) source=source.slice(0,start)+replacement+source.slice(end);
  return source;
}
function write(relative, contents) {
  const dest = path.join(output, relative);
  fs.mkdirSync(path.dirname(dest), {recursive:true});
  fs.writeFileSync(dest, contents);
}
for (const relative of assets) {
  const source = fs.readFileSync(path.join(root,relative));
  hashes[relative] = crypto.createHash('sha256').update(source).digest('hex');
  if (relative.endsWith('.js')) {
    const converted = esbuild.transformSync(lowerUnicodeRegex(source.toString('utf8')),{target,loader:'js',charset:'utf8'}).code;
    acorn.parse(converted,{ecmaVersion:2017});
    write(relative,converted);
  } else if (relative === 'index.html') {
    let html = source.toString('utf8');
    html = html.replace('<div id="app"></div>', '<div id="app"><div class="empty">正在打开 MOSS…</div></div>');
    html = html.replace('</head>', '<link rel="stylesheet" href="compat.css"></head>');
    html = html.replace('<script src="vendor/marked.js">', '<script src="boot.js"></script><script src="compat.js"></script><script src="abort-polyfill.js"></script><script src="port-host.js"></script><script src="vendor/marked.js">');
    write(relative,html);
  } else if (relative.endsWith('.css')) {
    write(relative,esbuild.transformSync(source.toString('utf8'),{target,loader:'css',charset:'utf8'}).code);
  } else write(relative,source);
}
for (const relative of ['boot.js','compat.js','port-host.js','compat.css']) {
  const source = fs.readFileSync(path.join(root,'android-support',relative));
  hashes['android-support/'+relative] = crypto.createHash('sha256').update(source).digest('hex');
  write(relative,esbuild.transformSync(source.toString('utf8'),{target,loader:relative.endsWith('.js')?'js':'css',charset:'utf8'}).code);
  assets.push(relative);
}
write('abort-polyfill.js', fs.readFileSync(require.resolve('abortcontroller-polyfill/dist/polyfill-patch-fetch')));
write('abort-polyfill-LICENSE', fs.readFileSync(path.join(path.dirname(require.resolve('abortcontroller-polyfill/package.json')),'LICENSE')));
assets.push('abort-polyfill.js','abort-polyfill-LICENSE');
write('assets.json', JSON.stringify(assets,null,2)+'\n');
write('compat-build.json', JSON.stringify({target:'chrome61',esbuild:esbuild.version,sourceHashes:hashes},null,2)+'\n');
console.log('Android compatibility bundle: '+assets.length+' assets, target Chrome 61');
