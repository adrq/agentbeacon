import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkRehype from 'remark-rehype';
import rehypeShiki from '@shikijs/rehype';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import rehypeStringify from 'rehype-stringify';

type HastNode = { type: string; tagName?: string; value?: string; properties?: Record<string, unknown>; children?: HastNode[] };

// Style attributes are allowed only for shiki's inline CSS variables (--shiki-light, etc.).
// Safe because remarkRehype does NOT pass through raw HTML (no allowDangerousHtml),
// so only shiki-generated nodes produce style attributes.
// dataMermaid: marker attribute for mermaid blocks, converted to class="mermaid" post-sanitize.
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    span: [...(defaultSchema.attributes?.span || []), 'style'],
    pre: [...(defaultSchema.attributes?.pre || []), 'style', 'dataMermaid'],
    code: [...(defaultSchema.attributes?.code || []), 'style'],
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

let processorPromise: ReturnType<typeof createProcessor> | null = null;

async function createProcessor() {
  return unified()
    .use(remarkParse)
    .use(remarkGfm)
    .use(remarkRehype)
    .use(rehypeMermaidPre)
    .use(rehypeShiki, {
      themes: { light: 'github-light', dark: 'github-dark' },
      defaultColor: false,
      langs: ['js', 'ts', 'python', 'bash', 'json', 'rust', 'yaml', 'xml', 'css', 'sql', 'go', 'toml', 'markdown'],
    })
    .use(rehypeSanitize, sanitizeSchema)
    .use(rehypeRestoreClasses)
    .use(rehypeStringify);
}

function getProcessor() {
  if (!processorPromise) {
    processorPromise = createProcessor().catch((err) => {
      processorPromise = null;
      throw err;
    });
  }
  return processorPromise;
}

// Cache rendered HTML to avoid re-running the unified pipeline for the same text.
// Agent messages are immutable (append-only events), so cache entries are never
// invalidated. Bounded to 500 entries as a safety net against unbounded growth.
const renderCache = new Map<string, string>();
const CACHE_MAX = 500;

export async function renderMarkdown(text: string, skipCache = false): Promise<string> {
  if (!skipCache) {
    const cached = renderCache.get(text);
    if (cached !== undefined) return cached;
  }

  const processor = await getProcessor();
  const result = String(await processor.process(text));

  if (!skipCache) {
    if (renderCache.size >= CACHE_MAX) {
      const firstKey = renderCache.keys().next().value!;
      renderCache.delete(firstKey);
    }
    renderCache.set(text, result);
  }

  return result;
}
