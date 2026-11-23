// 台帳の画像を Sepolia(テストネット)の NFT として記録する。署名と送信は MetaMask(秘密鍵はアプリに来ない)。
// 契約は contracts/ReleaseRecord.sol(npm run compile:nft で nft/ReleaseRecord.json を作る)。
// ethers はこのページを開いたときだけ読み込む。
import artifact from './nft/ReleaseRecord.json'

export const SEPOLIA_CHAIN_ID = 11155111
const SEPOLIA_HEX = '0xaa36a7'

interface Eip1193 {
  request(args: { method: string; params?: unknown[] | object }): Promise<unknown>
}

function ethereum(): Eip1193 {
  const injected = (window as unknown as { ethereum?: Eip1193 }).ethereum
  if (!injected) throw new Error('MetaMask が見つかりません。ブラウザに MetaMask を入れて、このページを開き直してください。')
  return injected
}

export function hasWallet(): boolean {
  return Boolean((window as unknown as { ethereum?: unknown }).ethereum)
}

/** MetaMask につなぎ、ネットワークを Sepolia にする。つないだアドレスを返す */
export async function connect(): Promise<string> {
  const eth = ethereum()
  const accounts = (await eth.request({ method: 'eth_requestAccounts' })) as string[]
  if (!accounts?.length) throw new Error('MetaMask のアカウントを選んでください。')
  const chain = (await eth.request({ method: 'eth_chainId' })) as string
  if (parseInt(chain, 16) !== SEPOLIA_CHAIN_ID) {
    try {
      await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: SEPOLIA_HEX }] })
    } catch {
      throw new Error('MetaMask のネットワークを Sepolia(テストネット)に切り替えてください。')
    }
  }
  return accounts[0].toLowerCase()
}

async function signer() {
  const { BrowserProvider } = await import('ethers')
  const provider = new BrowserProvider(ethereum() as never)
  const network = await provider.getNetwork()
  // 本物のお金がかかるチェーンでは送らない
  if (Number(network.chainId) !== SEPOLIA_CHAIN_ID) {
    throw new Error('Sepolia(テストネット)以外では使えません。MetaMask のネットワークを確かめてください。')
  }
  return provider.getSigner()
}

/** 記録の契約を作る(初回だけ)。戻り値はアドレス・取引の ID・持ち主 */
export async function deployContract(): Promise<{ address: string; txHash: string; owner: string }> {
  const { ContractFactory } = await import('ethers')
  const s = await signer()
  const factory = new ContractFactory(artifact.abi, artifact.bytecode, s)
  const contract = await factory.deploy('Release records', 'RREC')
  const tx = contract.deploymentTransaction()
  await contract.waitForDeployment()
  return {
    address: (await contract.getAddress()).toLowerCase(),
    txHash: tx?.hash ?? '',
    owner: (await s.getAddress()).toLowerCase(),
  }
}

export interface MintItem {
  mark_id: number
  sha256: string
  phash: string
  released_at: number
}

/** 記録をまとめて発行する(1回の取引)。取引が確定したら取引の ID を返す */
export async function mintBatch(contractAddress: string, items: MintItem[]): Promise<string> {
  const { Contract } = await import('ethers')
  const s = await signer()
  const contract = new Contract(contractAddress, artifact.abi, s)
  const owner = String(await contract.owner()).toLowerCase()
  const me = (await s.getAddress()).toLowerCase()
  if (owner !== me) {
    throw new Error(`MetaMask のアカウントが契約の持ち主(${owner})と違います。アカウントを切り替えてください。`)
  }
  const tx = await contract.mintBatch(
    items.map(i => BigInt(i.mark_id)),
    items.map(i => i.sha256),
    items.map(i => BigInt(i.phash)),
    items.map(i => BigInt(i.released_at)),
  )
  const receipt = await tx.wait()
  if (!receipt || receipt.status !== 1) throw new Error('取引が失敗しました。')
  return tx.hash as string
}
