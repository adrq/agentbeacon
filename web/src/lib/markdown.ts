// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { unified, type PluggableList } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkRehype from 'remark-rehype';
import rehypeShiki from '@shikijs/rehype';
import rehypeSanitize, { defaultSchema, type Options as SanitizeSchema } from 'rehype-sanitize';
import rehypeStringify from 'rehype-stringify';

type HastNode = { type: string; tagName?: string; value?: string; properties?: Record<string, unknown>; children?: HastNode[] };

// Style attributes are allowed only for shiki's inline CSS variables (--shiki-light, etc.).
// Safe because remarkRehype does NOT pass through raw HTML (no allowDangerousHtml),
// so only shiki-generated nodes produce style attributes.
// dataMermaid: marker attribute for mermaid blocks, converted to class="mermaid" post-sanitize.
//
// The code[className] tuple restates the default schema's `['className', /^language-./]`
// rather than appending a second className entry: hast-util-sanitize resolves an
// attribute against the FIRST matching definition, so a duplicate tuple would be dead.
//
// math-inline/math-display are belt-and-braces, not load-bearing. remark-math emits
// `class="language-math math-display"`, and `language-math` already matches the
// /^language-./ above, which rehype-katex accepts on its own — mutation-tested, so
// don't reintroduce this as a hazard from reading the code. What IS load-bearing is
// that rehypeKatex runs AFTER rehypeSanitize; reversing that order breaks all math.
const sanitizeSchema: SanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    span: [...(defaultSchema.attributes?.span || []), 'style'],
    pre: [...(defaultSchema.attributes?.pre || []), 'style', 'dataMermaid'],
    code: [['className', /^language-./, 'math-inline', 'math-display'], 'style'],
  },
};

function extractText(node: HastNode): string {
  if (node.type === 'text') return node.value ?? '';
  if (node.children) return node.children.map(extractText).join('');
  return '';
}

// Convert ```mermaid code blocks into <pre data-mermaid>text</pre> before shiki
// runs, so shiki doesn't try to highlight them (mermaid isn't a programming language).
function rehypeMermaidPre() {
  return (tree: HastNode) => {
    function visit(node: HastNode) {
      if (
        node.type === 'element' &&
        node.tagName === 'pre' &&
        node.children?.length === 1 &&
        node.children[0].type === 'element' &&
        node.children[0].tagName === 'code'
      ) {
        const codeNode = node.children[0];
        const classes = codeNode.properties?.className;
        if (Array.isArray(classes) && classes.includes('language-mermaid')) {
          const text = extractText(codeNode);
          node.properties = { dataMermaid: '' };
          node.children = [{ type: 'text', value: text }];
          return;
        }
      }
      if (node.children) node.children.forEach(visit);
    }
    visit(tree);
  };
}

// rehype-sanitize strips className even when allowed in schema (hast-util-sanitize quirk).
// Restore the .shiki class on <pre> elements that have shiki CSS variables in style.
// Also convert data-mermaid marker to class="mermaid" for client-side rendering.
// SAFETY: Only adds hardcoded constants — must NEVER use user-derived values.
function rehypeRestoreClasses() {
  return (tree: HastNode) => {
    function visit(node: HastNode) {
      if (node.type === 'element' && node.tagName === 'pre') {
        const style = String(node.properties?.style ?? '');
        if (style.includes('--shiki-')) {
          const existing = Array.isArray(node.properties!.className) ? node.properties!.className : [];
          node.properties!.className = [...existing, 'shiki'];
        }
        if (node.properties?.dataMermaid !== undefined) {
          node.properties.className = ['mermaid'];
          delete node.properties.dataMermaid;
        }
      }
      if (node.children) (node.children as HastNode[]).forEach(visit);
    }
    visit(tree);
  };
}

// Cheap pre-check on the raw source: does this text plausibly contain TeX-style
// (`\[`, `\(`) or display-dollar math? Very few messages do, and the
// math extension drags in ~280KB of katex, so the math plugins load lazily and only
// for texts that match. False positives (e.g. `\[` inside a fenced block) are harmless:
// they just build the heavier processor, they don't change the output.
const MATH_HINT = /\\[[(]|\$\$/;

async function loadMathPlugins(): Promise<{ remark: PluggableList; rehype: PluggableList }> {
  // KaTeX's stylesheet is required for correct layout with the default htmlAndMathml
  // output; importing it here keeps it out of the main chunk alongside the JS.
  const [remarkMath, rehypeKatex] = await Promise.all([
    import('remark-math-extended'),
    import('rehype-katex'),
    import('katex/dist/katex.min.css'),
  ]);
  return {
    // Single-dollar text math stays off: `$state`, `$AGENTBEACON_API_BASE` and `$1.2M`
    // are common here, and dollar-delimited math is not something these
    // models actually emit.
    remark: [[remarkMath.default, { singleDollarTextMath: false }]],
    rehype: [rehypeKatex.default],
  };
}

// shiki: syntax-highlight code blocks (skipped while streaming — too slow per token).
// math: parse TeX delimiters and expand them with katex.
type ProcessorVariant = { shiki: boolean; math: boolean };

async function createProcessor({ shiki, math }: ProcessorVariant) {
  const mathPlugins = math ? await loadMathPlugins() : { remark: [], rehype: [] };
  const shikiPlugins: PluggableList = shiki
    ? [
        [
          rehypeShiki,
          {
            themes: { light: 'github-light', dark: 'github-dark' },
            defaultColor: false,
            langs: ['js', 'ts', 'python', 'bash', 'json', 'rust', 'yaml', 'xml', 'css', 'sql', 'go', 'toml', 'markdown'],
          },
        ],
      ]
    : [];

  return (
    unified()
      .use(remarkParse)
      .use(mathPlugins.remark)
      .use(remarkGfm)
      .use(remarkRehype)
      .use(rehypeMermaidPre)
      .use(shikiPlugins)
      .use(rehypeSanitize, sanitizeSchema)
      // After sanitize, per the rehype-katex README: katex reads only the text content
      // of already-sanitized nodes, so its MathML/HTML output needs no schema allowance.
      .use(mathPlugins.rehype)
      .use(rehypeRestoreClasses)
      .use(rehypeStringify)
  );
}

const processorPromises = new Map<string, ReturnType<typeof createProcessor>>();

function getProcessor(variant: ProcessorVariant) {
  const key = `${variant.shiki ? 'shiki' : 'plain'}:${variant.math ? 'math' : 'nomath'}`;
  let promise = processorPromises.get(key);
  if (!promise) {
    promise = createProcessor(variant).catch((err) => {
      console.error(`Markdown processor (${key}) initialization failed:`, err);
      processorPromises.delete(key);
      throw err;
    });
    processorPromises.set(key, promise);
  }
  return promise;
}

// Cache rendered HTML to avoid re-running the unified pipeline for the same text.
// Agent messages are immutable (append-only events), so cache entries are never
// invalidated. Bounded to 500 entries as a safety net against unbounded growth.
const renderCache = new Map<string, string>();
const CACHE_MAX = 500;

export async function renderMarkdown(text: string, skipCache = false, streaming = false): Promise<string> {
  if (!skipCache && !streaming) {
    const cached = renderCache.get(text);
    if (cached !== undefined) return cached;
  }

  const processor = await getProcessor({ shiki: !streaming, math: MATH_HINT.test(text) });
  const result = String(await processor.process(text));

  if (!skipCache && !streaming) {
    if (renderCache.size >= CACHE_MAX) {
      const firstKey = renderCache.keys().next().value!;
      renderCache.delete(firstKey);
    }
    renderCache.set(text, result);
  }

  return result;
}
