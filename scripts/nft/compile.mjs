// contracts/ReleaseRecord.sol をコンパイルして、画面で使う ABI とバイトコードを書き出す。
//   npm run compile:nft
// 出力: src/javascript/nft/ReleaseRecord.json(コミットする。画面のビルドではコンパイルしない)
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import solc from 'solc'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const source = readFileSync(join(root, 'contracts', 'ReleaseRecord.sol'), 'utf8')

function findImports(path) {
  try {
    return { contents: readFileSync(require.resolve(path), 'utf8') }
  } catch {
    return { error: `not found: ${path}` }
  }
}

const input = {
  language: 'Solidity',
  sources: { 'ReleaseRecord.sol': { content: source } },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    evmVersion: 'paris',
    outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } },
  },
}

const output = JSON.parse(solc.compile(JSON.stringify(input), { import: findImports }))
const errors = (output.errors ?? []).filter(e => e.severity === 'error')
if (errors.length) {
  for (const e of errors) console.error(e.formattedMessage)
  process.exit(1)
}
const contract = output.contracts['ReleaseRecord.sol'].ReleaseRecord
const outDir = join(root, 'src', 'javascript', 'nft')
mkdirSync(outDir, { recursive: true })
writeFileSync(
  join(outDir, 'ReleaseRecord.json'),
  JSON.stringify({ compiler: solc.version(), abi: contract.abi, bytecode: `0x${contract.evm.bytecode.object}` }, null, 2) + '\n',
)
console.log(`ReleaseRecord: ${contract.evm.bytecode.object.length / 2} bytes (${solc.version()})`)
