// ESLint's recommended rules with typescript-eslint's type-checked rules on top: Atlas's
// TypeScript is held as strictly as its Python is by pyright.
import js from '@eslint/js'
import perfectionist from 'eslint-plugin-perfectionist'
import hooksPlugin from 'eslint-plugin-react-hooks'
import unusedImports from 'eslint-plugin-unused-imports'
import globals from 'globals'
import tseslint from 'typescript-eslint'

/** Sorting, wherever a rule sorts: ascending, with numbers in a name compared as numbers. */
const NATURAL = { order: 'asc', type: 'natural' }
/** Imports in blocks, nearest last: bare side effects, Node, packages, `@/`, then relative paths. */
const IMPORT_BLOCKS = ['side-effect', 'builtin', 'external', 'internal', 'parent', 'sibling', 'index']

export default tseslint.config(
  {
    ignores: ['node_modules/**', 'out/**', 'test-results/**', 'playwright-report/**', '**/*.generated.ts']
  },
  js.configs.recommended,
  {
    extends: [...tseslint.configs.strictTypeChecked],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parserOptions: {
        project: ['./tsconfig.json', './tsconfig.electron.json', './tsconfig.e2e.json'],
        tsconfigRootDir: import.meta.dirname
      }
    },
    plugins: {
      'unused-imports': unusedImports,
      'react-hooks': hooksPlugin,
      perfectionist
    },
    rules: {
      // Every `if` and loop body in braces, even a one-line return.
      curly: ['error', 'all'],
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],
      'unused-imports/no-unused-imports': 'error',
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
      'perfectionist/sort-imports': ['error', { ...NATURAL, groups: IMPORT_BLOCKS }],
      'perfectionist/sort-named-imports': ['error', NATURAL],
      'perfectionist/sort-jsx-props': ['error', NATURAL]
    }
  },
  {
    // A ref that an effect copies a store into is one render behind it: a callback reading that ref
    // sees the old value, and an interrupt could land on the wrong session. A callback reads the
    // store itself.
    files: ['src/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          message: 'An effect must not copy a store into a ref; the callback calls $store.get() itself.',
          selector:
            'CallExpression[callee.name="useEffect"] > ArrowFunctionExpression[body.type="BlockStatement"]:has(AssignmentExpression[left.type="MemberExpression"][left.property.name="current"])'
        }
      ]
    }
  },
  {
    files: ['**/*.{js,mjs,cjs}'],
    languageOptions: { globals: { ...globals.node } }
  }
)
