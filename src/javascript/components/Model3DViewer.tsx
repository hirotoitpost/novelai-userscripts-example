import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

/**
 * 素体の 3D モデル(GLB)を見る。ドラッグで回す・ホイールで寄る。GLB に入った動き(骨のアニメーション)を選んで再生する。
 * 形状パーツ(GLB のモーフ = Blender のシェイプキー)には shapes の強さを当てる(書いていない形は 0)。
 * three.js は大きいので、Model3D から必要になったときだけ読み込む(React.lazy)。
 */

interface Props {
  src: string
  /** 再生する動きの名前(GLB のアニメーション。null なら止めて立った姿勢) */
  animation: string | null
  /** 読み込んだ GLB に入っていた動きの名前 */
  onAnimations?: (names: string[]) => void
  /** 形状パーツの強さ(形の名前 → 0〜2) */
  shapes?: Record<string, number>
  /**
   * カメラの位置と注目点を覚えておく入れ物(呼ぶ側が持つ)。あれば、読み込み直しても(形の値の試し・モデル本体の
   * 切り替えなど)そこから表示し、回すたびに書き換える。無い・空なら、全身が入る位置から始める
   */
  view?: { current: CameraView | null }
}

export interface CameraView {
  position: [number, number, number]
  target: [number, number, number]
}

export default function Model3DViewer({ src, animation, onAnimations, shapes, view }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const mixerRef = useRef<THREE.AnimationMixer | null>(null)
  const morphRef = useRef<THREE.Mesh[]>([])
  // 着脱する部品(乳首・乳輪。part_<形>_<L|R>)。選んでいない形は隠す
  const partsRef = useRef<THREE.Object3D[]>([])
  const clipsRef = useRef<THREE.AnimationClip[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const width = host.clientWidth || 480
    const height = Math.round(width * 1.25)
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(width, height)
    host.appendChild(renderer.domElement)
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0xf2f2f2)
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8a8a, 2.2))
    const sun = new THREE.DirectionalLight(0xffffff, 1.6)
    sun.position.set(1.5, 3, 2.5)
    scene.add(sun)
    const grid = new THREE.GridHelper(4, 20, 0xbbbbbb, 0xdddddd)
    scene.add(grid)
    const camera = new THREE.PerspectiveCamera(35, width / height, 0.01, 100)
    camera.position.set(0, 1, 4)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.addEventListener('change', () => {
      if (view) view.current = { position: camera.position.toArray(), target: controls.target.toArray() }
    })
    const clock = new THREE.Clock()
    let frame = 0
    let disposed = false

    function tick() {
      frame = requestAnimationFrame(tick)
      mixerRef.current?.update(clock.getDelta())
      controls.update()
      renderer.render(scene, camera)
    }

    setLoading(true)
    setError(null)
    new GLTFLoader().load(
      src,
      gltf => {
        if (disposed) return
        const model = gltf.scene
        // 形だけの GLB は材質が無いので、灰色にする
        model.traverse(obj => {
          const mesh = obj as THREE.Mesh
          if (mesh.isMesh) {
            mesh.material = new THREE.MeshStandardMaterial({ color: 0xcfcfcf, roughness: 0.8 })
            mesh.frustumCulled = false
          }
        })
        const morphs: THREE.Mesh[] = []
        model.traverse(obj => {
          const mesh = obj as THREE.Mesh
          if (mesh.isMesh && mesh.morphTargetDictionary) morphs.push(mesh)
        })
        morphRef.current = morphs
        const parts: THREE.Object3D[] = []
        model.traverse(obj => {
          if (/^part_.+_[LR]$/.test(obj.name)) parts.push(obj)
        })
        partsRef.current = parts
        scene.add(model)
        // 足の裏から頭の上までが入るよう、カメラを置く
        const box = new THREE.Box3().setFromObject(model)
        const size = box.getSize(new THREE.Vector3())
        const center = box.getCenter(new THREE.Vector3())
        if (view?.current) {
          // 前に見ていた位置から(読み込み直しても、回した向き・寄った具合を保つ)
          controls.target.fromArray(view.current.target)
          camera.position.fromArray(view.current.position)
        } else {
          controls.target.copy(center)
          camera.position.set(center.x, center.y + size.y * 0.1, center.z + size.y * 2.2)
        }
        controls.update()
        mixerRef.current = gltf.animations.length ? new THREE.AnimationMixer(model) : null
        clipsRef.current = gltf.animations
        onAnimations?.(gltf.animations.map(clip => clip.name))
        setLoading(false)
      },
      undefined,
      e => {
        if (disposed) return
        setError(e instanceof Error ? e.message : String(e))
        setLoading(false)
      },
    )
    tick()
    return () => {
      disposed = true
      cancelAnimationFrame(frame)
      controls.dispose()
      renderer.dispose()
      host.removeChild(renderer.domElement)
      mixerRef.current = null
    }
  }, [src])

  useEffect(() => {
    const mixer = mixerRef.current
    if (!mixer) return
    mixer.stopAllAction()
    const clip = clipsRef.current.find(c => c.name === animation)
    if (clip) mixer.clipAction(clip).reset().play()
  }, [animation, loading])

  useEffect(() => {
    if (!shapes) return
    for (const mesh of morphRef.current) {
      const names = mesh.morphTargetDictionary ?? {}
      const influences = mesh.morphTargetInfluences
      if (!influences) continue
      for (const [name, index] of Object.entries(names)) influences[index] = shapes[name] ?? 0
    }
    for (const part of partsRef.current) part.visible = (shapes[part.name.slice(5, -2)] ?? 0) > 0
  }, [shapes, loading])

  return (
    <div className="m3d-viewer">
      <div ref={hostRef} className="m3d-viewer-canvas" />
      {loading && <p className="lora-hint">読み込んでいます…</p>}
      {error && <p className="lora-error">3D を読み込めませんでした: {error}</p>}
    </div>
  )
}
