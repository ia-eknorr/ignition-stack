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
    hooks: {
      onBrokenMarkdownLinks: "throw",
    },
  },

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
            { label: "Getting started", to: "/" },
            { label: "CLI reference", to: "/reference/cli" },
            { label: "Profiles", to: "/profiles/" },
            { label: "Services", to: "/services/" },
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
  } satisfies Preset.ThemeConfig,
};

export default config;
