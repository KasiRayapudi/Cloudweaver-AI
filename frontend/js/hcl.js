/**
 * HCL tokeniser for the Terraform viewer.
 *
 * Hand-written rather than pulled from a highlighting library: the whole
 * grammar we need is eight token classes, and a library would add far more
 * bytes than the ~120 lines below while still needing a Terraform mode.
 *
 * Tokens are returned as data and turned into elements by the caller, so no
 * generated code ever reaches the DOM as markup.
 */

const KEYWORDS = new Set([
  "resource", "data", "variable", "output", "locals", "module", "provider",
  "terraform", "required_providers", "required_version", "backend", "moved",
  "import", "check", "removed",
]);

const CONSTANTS = new Set(["true", "false", "null"]);

// Functions the generator actually emits, so a name is only highlighted when
// it really is a call.
const FUNCTIONS = new Set([
  "merge", "jsonencode", "file", "filebase64", "filebase64sha256", "cidrsubnet",
  "substr", "trimsuffix", "format", "join", "split", "lookup", "length",
  "toset", "tolist", "tomap", "try", "coalesce", "concat", "element", "keys",
  "values", "base64encode", "templatefile", "max", "min", "abs",
]);

/**
 * Split one line into typed tokens.
 *
 * Line-based rather than whole-file: the viewer renders one row per line for
 * the gutter and folding to work, and a stateful multi-line lexer would have
 * to be re-run from the top every time a fold changed.
 */
export function tokeniseLine(line) {
  const tokens = [];
  let index = 0;

  while (index < line.length) {
    const rest = line.slice(index);

    // Comment: everything to end of line.
    const comment = rest.match(/^(#|\/\/).*/);
    if (comment) {
      tokens.push({ type: "comment", text: comment[0] });
      break;
    }

    // String, including "${...}" interpolation which is highlighted whole.
    const string = rest.match(/^"(?:[^"\\]|\\.)*"?/);
    if (string) {
      tokens.push({ type: "string", text: string[0] });
      index += string[0].length;
      continue;
    }

    // Number, including CIDR-ish and version-ish runs.
    const number = rest.match(/^-?\d+(?:\.\d+)*/);
    if (number && !/[\w.]$/.test(line.slice(0, index))) {
      tokens.push({ type: "number", text: number[0] });
      index += number[0].length;
      continue;
    }

    // Identifier, keyword, function call, or attribute name.
    const word = rest.match(/^[A-Za-z_][\w-]*/);
    if (word) {
      const value = word[0];
      const after = rest.slice(value.length);
      let type = "ident";

      if (KEYWORDS.has(value) && index === indentOf(line)) type = "keyword";
      else if (CONSTANTS.has(value)) type = "constant";
      else if (FUNCTIONS.has(value) && after.startsWith("(")) type = "function";
      else if (/^\s*=/.test(after) && !after.startsWith("==")) type = "attr";
      else if (/^(var|local|each|count|path|data)$/.test(value)) type = "builtin";

      tokens.push({ type, text: value });
      index += value.length;
      continue;
    }

    // Operators and punctuation, grouped so a run renders as one span.
    const punct = rest.match(/^[{}[\]()=,:?<>!+\-*/%&|]+/);
    if (punct) {
      tokens.push({ type: "punct", text: punct[0] });
      index += punct[0].length;
      continue;
    }

    const whitespace = rest.match(/^\s+/);
    if (whitespace) {
      tokens.push({ type: "plain", text: whitespace[0] });
      index += whitespace[0].length;
      continue;
    }

    tokens.push({ type: "plain", text: rest[0] });
    index += 1;
  }

  return tokens;
}

function indentOf(line) {
  return line.length - line.trimStart().length;
}

/**
 * Find the top-level blocks in a file so they can be folded and listed.
 *
 * Brace depth is tracked rather than matched with a regex because a block's
 * body contains braces of its own; depth returning to zero is what actually
 * ends a block.
 */
export function findBlocks(text) {
  const lines = text.split("\n");
  const blocks = [];
  let current = null;
  let depth = 0;

  lines.forEach((line, number) => {
    if (current === null) {
      const header = line.match(
        /^(resource|data|variable|output|module|provider|locals|terraform)\s*(?:"([^"]+)")?\s*(?:"([^"]+)")?/,
      );
      if (header) {
        current = {
          kind: header[1],
          type: header[2] || "",
          name: header[3] || header[2] || header[1],
          start: number,
          end: number,
        };
      }
    }

    depth += (line.match(/\{/g) || []).length;
    depth -= (line.match(/\}/g) || []).length;

    if (current && depth <= 0 && line.includes("}")) {
      current.end = number;
      blocks.push(current);
      current = null;
      depth = 0;
    }
  });

  if (current) {
    current.end = lines.length - 1;
    blocks.push(current);
  }
  return blocks;
}
