// The site's TypeScript held as strictly as the app's: ESLint's recommended rules with
// typescript-eslint's type-checked ones on top. The .astro files are checked by `astro check`.
import js from '@eslint/js'
import perfectionist from 'eslint-plugin-perfectionist'
import unusedImports from 'eslint-plugin-unused-imports'
import globals from 'globals'
import tseslint from 'typescript-eslint'

/** Sorting, wherever a rule sorts: ascending, with numbers in a name compared as numbers. */
const NATURAL = { order: 'asc', type: 'natural' }
/** Imports in blocks, nearest last: bare side effects, Node, packages, then relative paths. */
const IMPORT_BLOCKS = ['side-effect', 'builtin', 'external', 'internal', 'parent', 'sibling', 'index']

export default tseslint.config(
  {
    ignores: ['dist/**', '.astro/**', '.wrangler/**', 'node_modules/**']
  },
  js.configs.recommended,
  {
    extends: [...tseslint.configs.strictTypeChecked],
    files: ['**/*.ts'],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname
      }
    },
    plugins: {
      'unused-imports': unusedImports,
      perfectionist
    },
    rules: {
      curly: ['error', 'all'],
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],
      'unused-imports/no-unused-imports': 'error',
      'perfectionist/sort-imports': ['error', { ...NATURAL, groups: IMPORT_BLOCKS }],
      'perfectionist/sort-named-imports': ['error', NATURAL]
    }
  },
  {
    files: ['worker/**/*.ts'],
    languageOptions: { globals: { ...globals.serviceworker } }
  },
  {
    files: ['**/*.{js,mjs,cjs}'],
    languageOptions: { globals: { ...globals.node } }
  }
)
