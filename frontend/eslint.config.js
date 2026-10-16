import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'coverage'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // Unused arguments are often required by a signature; a leading
      // underscore marks them as deliberately ignored.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  {
    // Context providers are colocated with their `use*` hook and query keys,
    // which is the conventional shape for this pattern. The rule only guards
    // hot-reload granularity in development, so it is off for these files
    // rather than splitting every provider across two modules.
    files: ['src/lib/auth.tsx', 'src/lib/toast.tsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    // The router declares one lazy component per route and exports the router
    // itself, which is the whole point of the file. The rule only guards
    // hot-reload granularity; the alternative is a module per route whose only
    // content is a lazy() call.
    files: ['src/app/router.tsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'src/test/**'],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
  },
  {
    files: ['eslint.config.js', 'vite.config.ts'],
    languageOptions: { globals: globals.node },
  },
);
