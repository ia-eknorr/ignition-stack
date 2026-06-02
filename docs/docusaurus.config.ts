import type * as Preset from "@docusaurus/preset-classic";
import type { Config } from "@docusaurus/types";
import { themes as prismThemes } from "prism-react-renderer";

// The site is served from GitHub Pages at https://ia-eknorr.github.io/ignition-stack/.
// The deploy wiring itself lands in Phase 9; url/baseUrl are set here so internal
// links and asset paths resolve the same locally and in production.
const config: Config = {
  title: "ignition-stack",
  tagline:
    "Generate ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos and SE engagements.",
  url: "https://ia-eknorr.github.io",
  baseUrl: "/ignition-stack/",

  organizationName: "ia-eknorr",
  projectName: "ignition-stack",

  future: {
    v4: true,
    faster: true, // @docusaurus/faster: Rust/SWC build
  },

  // Fail the build on any broken internal link. The validation contract for
  // this site is "npm run build with no broken-link warnings", so anything
  // less than throw would let drift slip through.
  onBrokenLinks: "throw",
  onBrokenAnchors: "throw",

  markdown: {
    // Parse .md as CommonMark (lenient) and .mdx as MDX. The generated CLI
    // reference and service docs contain literal angle-bracket placeholders
    // like `<project>` inside code spans; MDX would try to parse those as JSX
    // and fail, whereas CommonMark leaves them untouched.
    format: "detect",
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: "throw",
    },
  },

  themes: [
    "@docusaurus/theme-mermaid",
    // Offline full-text search. This is the site's search function (no Algolia).
    // docsRouteBasePath must match the docs preset's routeBasePath ("/") for
    // docs-only mode, otherwise the indexer scans /docs/* and emits nothing.
    [
      require.resolve("@easyops-cn/docusaurus-search-local"),
      { hashed: true, indexBlog: false, docsRouteBasePath: "/" },
    ],
  ],

  plugins: [
    "@docusaurus/plugin-ideal-image",
    "docusaurus-plugin-image-zoom",
    [
      "@signalwire/docusaurus-plugin-llms-txt",
      {
        siteTitle: "ignition-stack",
        siteDescription:
          "Generate ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos and SE engagements.",
        depth: 2,
        content: { enableLlmsFullTxt: true },
      },
    ],
  ],

  i18n: {
    defaultLocale: "en",
    locales: ["en"],
  },

  presets: [
    [
      "classic",
      {
        docs: {
          // Docs-only mode: content is served at the site root rather than
          // under /docs, so the getting-started page is the landing page.
          routeBasePath: "/",
          sidebarPath: "./sidebars.ts",
          editUrl: "https://github.com/ia-eknorr/ignition-stack/tree/main/docs/",
        },
        blog: false,
        theme: {
          customCss: "./src/css/custom.css",
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    // Pin the mermaid theme per color mode so diagrams stay legible in dark mode.
    mermaid: {
      theme: { light: "default", dark: "dark" },
    },
    navbar: {
      title: "ignition-stack",
      items: [
        { type: "docSidebar", sidebarId: "docs", position: "left", label: "Docs" },
        {
          href: "https://github.com/ia-eknorr/ignition-stack",
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "Docs",
          items: [
            { label: "Quickstart", to: "/get-started/quickstart" },
            { label: "Profiles", to: "/profiles/" },
            { label: "Services", to: "/services/" },
            { label: "CLI reference", to: "/reference/cli" },
          ],
        },
        {
          title: "More",
          items: [
            { label: "GitHub", href: "https://github.com/ia-eknorr/ignition-stack" },
          ],
        },
      ],
      copyright: "Inductive Automation",
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ["bash", "yaml", "json", "makefile", "docker"],
    },
    // Click-to-zoom for docusaurus-plugin-image-zoom.
    zoom: {
      selector: ".markdown img",
      background: { light: "rgb(255, 255, 255)", dark: "rgb(50, 50, 50)" },
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
