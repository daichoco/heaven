const fs = require("node:fs");
const path = require("node:path");
const { html: beautify } = require("js-beautify");

const root = path.resolve(__dirname, "..");
const options = JSON.parse(fs.readFileSync(path.join(root, ".jsbeautifyrc"), "utf8")).html;

function formatHtml(source) {
  return beautify(source, options);
}

function htmlFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name)).flatMap(entry => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? htmlFiles(file) : entry.isFile() && /\.html$/i.test(entry.name) ? [file] : [];
  });
}

if (require.main === module) {
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === "--stdin") {
    process.stdout.write(formatHtml(fs.readFileSync(0, "utf8")));
  } else if (args.length === 0 || (args.length === 1 && args[0] === "--check")) {
    const check = args[0] === "--check";
    let changed = 0;
    const files = htmlFiles(path.join(root, "docs"));
    for (const file of files) {
      const source = fs.readFileSync(file, "utf8");
      const formatted = formatHtml(source);
      if (source !== formatted) {
        changed++;
        if (!check) fs.writeFileSync(file, formatted, "utf8");
        console.log(`${check ? "Needs formatting" : "Formatted"}: ${path.relative(root, file)}`);
      }
    }
    console.log(`${files.length} HTML files; ${changed} ${check ? "need formatting" : "changed"}.`);
    if (check && changed) process.exitCode = 1;
  } else {
    console.error("Usage: node scripts/format-html.cjs [--check|--stdin]");
    process.exitCode = 2;
  }
}

module.exports = { formatHtml };
