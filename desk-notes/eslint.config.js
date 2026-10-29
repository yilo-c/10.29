// 思忆便签 ESLint 配置（T3.3，2026-08-22）
// Flat config + typescript-eslint。
//
// 设计原则（与后端 ruff 基线策略一致）：
//   起步阶段只启用"明确安全、不会误伤存量"的核心规则为 error，
//   其余保持关闭或 warn，避免一次性 lint 出上千存量错误把 CI 炸红。
//   后续随存量清理，再逐步把 warn 提升为 error、开启更多规则。
//
// 运行：pnpm lint / pnpm lint:fix

import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  {
    ignores: [
      "**/node_modules/**",
      "**/dist/**",
      "**/build/**",
      "**/.vite/**",
      "**/release/**",
      "**/*.cjs", // electron main/preload 为 CommonJS，单独处理
      "scripts/**",
      "**/coverage/**",
      "**/*.bak*/**", // 时间戳备份目录（如 FloatingBubble.bak.20260518_062112）不入检
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      // typescript-eslint 推荐集已关闭 no-undef（改用类型感知），
      // 此处显式关闭以防 globals 缺失误报浏览器/React 全局
      "no-undef": "off",
      // —— React Hooks 规则（存量已有 14 处 eslint-disable 注释引用，需插件生效）——
      // 存量违规较多（exhaustive-deps 68 / rules-of-hooks 3），先 warn 不阻塞 CI，
      // 随存量清理后再逐步收紧为 error
      "react-hooks/rules-of-hooks": "warn",
      "react-hooks/exhaustive-deps": "warn",
      // —— 核心 error 级（安全、低误伤）——
      "no-duplicate-imports": "error",
      // 未使用变量交由 tsc 的 noUnusedLocals 处理，ESLint 仅 warn 不阻塞
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": "warn",
      "@typescript-eslint/no-explicit-any": "off", // 存量大量 any，后续收紧
      "@typescript-eslint/ban-ts-comment": "off",
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/no-empty-object-type": "off",
      "no-console": "off", // 桌面端调试日志常见，不阻塞
    },
  },
  {
    // ── C-22 fontSize token 防新增 ──
    // 目标：阻止在 .tsx 里新写「命中 fontSize 档位」的任意值 text-[Npx]，
    // 强制走 token 类（见 packages/desktop/tailwind.config.js:88-101）。
    // 档位映射(px→class)：10=2xs 11=xs 12=sm 13=base 14=md 16=lg 18=xl 22=2xl 24=display 28=display-lg 32=3xl
    // 白名单(刻意微字号，scale 外，不报也不映射)：5/6/7/8/9/15px —— 便签类 App 的紧凑设计，强升会破版。
    //   存量：text-[5px]×1 [6]×12 [7]×45 [8]×209 [9]×21 [15]×3。
    // 内联 fontSize：本批全部保留（嵌在多属性 style 对象里，且 mobile 无 Tailwind、用 CSS 变量 token，
    //   迁 class 属结构改动、非机械替换）。存量登记：in-scale 41 / out-scale 9×6 15×1 20×1 26×2。
    // 仅告警不阻断 CI（排除清单 NoteEditor/ContentRouter/MainWindow/App 存量仍含 in-scale，error 会误伤）。
    // no-restricted-syntax 非 fixable，`eslint --fix` 不会改写 className → 不误伤。
    files: ["packages/**/*.{jsx,tsx}", "**/*.tsx"],
    rules: {
      "no-restricted-syntax": [
        "warn",
        {
          selector:
            "Literal[value=/text-\\[(10|11|12|13|14|16|18|22|24|28|32)px\\]/]",
          message:
            "fontSize 请用语义 token 类（10=text-2xs 11=text-xs 12=text-sm 13=text-base 14=text-md 16=text-lg 18=text-xl 22=text-2xl 24=text-display 28=text-display-lg 32=text-3xl）；微字号 5/6/7/8/9/15px 属白名单。",
        },
        {
          selector:
            "TemplateElement[value.raw=/text-\\[(10|11|12|13|14|16|18|22|24|28|32)px\\]/]",
          message:
            "fontSize 请用语义 token 类（见 tailwind.config.js 档位表）；微字号 5/6/7/8/9/15px 属白名单。",
        },
      ],
    },
  },
  {
    // Electron 主进程（CommonJS），仅做最基础检查
    files: ["electron/**/*.cjs", "electron/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "commonjs",
      globals: {
        require: "readonly",
        module: "readonly",
        __dirname: "readonly",
        process: "readonly",
        console: "readonly",
      },
    },
    rules: {
      "no-undef": "error",
      "no-duplicate-imports": "error",
    },
  },
  {
    // 桌面端独立脚本（Node 侧工具 / Playwright 等注入浏览器上下文），
    // 显式声明 Node 与浏览器全局，消除 no-undef 误报
    files: [
      "packages/desktop/scripts/**/*.mjs",
      "packages/desktop/test-playwright.mjs",
    ],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        console: "readonly",
        process: "readonly",
        window: "readonly",
        document: "readonly",
        getComputedStyle: "readonly",
      },
    },
    rules: {
      "no-undef": "error",
    },
  },
);
