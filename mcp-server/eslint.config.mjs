import js from "@eslint/js";
import tseslint from "typescript-eslint";

// Non type-aware on purpose: the pre-commit hook runs eslint from its own
// isolated environment, where no node_modules or built tsconfig project is
// available. `make mcp typecheck` covers what type-aware rules would catch.
export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.ts"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_" },
      ],
      "no-console": "off",
      eqeqeq: ["error", "always"],
    },
  },
);
