import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const route = readFileSync(fileURLToPath(new URL('./route.ts', import.meta.url)), 'utf8')
const css = readFileSync(fileURLToPath(new URL('./tailwind.css', import.meta.url)), 'utf8')
const pkg = JSON.parse(readFileSync(fileURLToPath(new URL('../package.json', import.meta.url)), 'utf8'))

assert.match(route, /import ['"]\.\/tailwind\.css['"]/, 'plugin route must import its Tailwind entry')
assert.match(
  pkg.devDependencies?.tailwindcss ?? '',
  /^\^?4\./,
  'Tailwind package must be available to resolve theme imports',
)
const layerOrder = css.indexOf('@layer theme, base, components, utilities;')
assert.notEqual(layerOrder, -1, 'plugin entry must declare the host layer order')
assert.ok(layerOrder < css.indexOf("@import 'tailwindcss/theme'"), 'layer order must precede imported utilities')
assert.match(css, /@import ['"]tailwindcss\/theme['"] layer\(theme\)/)
assert.match(css, /@import ['"]tailwindcss\/utilities['"] layer\(utilities\)/)
assert.doesNotMatch(css, /@import ['"]tailwindcss['"]/, 'host already provides the Tailwind preflight')
for (const source of [
  './OdooWorkspace.tsx',
  './OdooTuiRoute.tsx',
  './LogWorkspace.tsx',
  './PasswordCapability.tsx',
  './Button.tsx',
]) {
  assert.ok(
    css.includes(`@source '${source}'`) || css.includes(`@source \"${source}\"`),
    `missing @source for ${source}`,
  )
}
for (const token of [
  'surface',
  'surface-raised',
  'surface-sunken',
  'border',
  'border-subtle',
  'text',
  'text-muted',
  'text-subtle',
  'accent',
  'accent-subtle',
  'positive',
  'positive-subtle',
  'warning',
  'negative',
  'negative-subtle',
]) {
  assert.ok(css.includes(`--color-${token}:`), `missing Mission Control theme token --color-${token}`)
}
console.log('Tailwind 4 plugin entry contract PASS')
