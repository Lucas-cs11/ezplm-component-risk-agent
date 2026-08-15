/** @type {import('next').NextConfig} */
const nextConfig = {
  images: { unoptimized: true },
  transpilePackages: [
    "react-markdown",
    "remark-gfm",
    "remark-parse",
    "unified",
    "bail",
    "is-plain-obj",
    "trough",
    "vfile",
    "vfile-message",
    "unist-util-stringify-position",
    "mdast-util-from-markdown",
    "mdast-util-to-string",
    "micromark",
    "decode-named-character-reference",
    "character-entities",
    "mdast-util-to-hast",
    "trim-lines",
    "unist-util-is",
    "unist-util-visit",
    "unist-util-visit-parents",
    "hast-util-to-jsx-runtime",
    "hast-util-whitespace",
    "property-information",
    "space-separated-tokens",
    "comma-separated-tokens",
    "remark-rehype",
    "rehype-raw",
    "hast-util-raw",
    "hast-util-from-parse5",
    "hast-util-to-parse5",
    "hastscript",
    "html-void-elements",
    "zwitch",
  ],
  async rewrites() {
    const backend = 'http://127.0.0.1:8000';
    const routes = [
      '/api/:path*',
      '/auth/:path*',
      '/admin/:path*',
      '/chat/:path*',
      '/health',
      '/export/:path*',
      '/models/:path*',
      '/sessions/:path*',
      '/mpn/:path*',
    ];
    return routes.map(source => ({
      source,
      destination: source.endsWith(':path*')
        ? `${backend}${source}`
        : `${backend}${source}`,
    }));
  },
};

module.exports = nextConfig;
