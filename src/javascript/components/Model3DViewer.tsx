import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

/**
 * 素体の 3D モデル(GLB)を見る。ドラッグで回す・ホイールで寄る。GLB に入った動き(骨のアニメーション)を選んで再生する。
 * three.js は大きいので、Model3D から必要になったときだけ読み込む(React.lazy)。
 */

interface Props {
  src: string
  /** 再生する動きの名前(GLB のアニメーション。null なら止めて立った姿勢) */
  animation: string | null
  /** 読み込んだ GLB に入っていた動きの名前 */
  onAnimations?: (names: string[]) => void
}

export default function Model3DViewer({ src, animation, onAnimations }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const mixerRef = useRef<THREE.AnimationMixer | null>(null)
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
        scene.add(model)
        // 足の裏から頭の上までが入るよう、カメラを置く
        const box = new THREE.Box3().setFromObject(model)
        const size = box.getSize(new THREE.Vector3())
        const center = box.getCenter(new THREE.Vector3())
        controls.target.copy(center)
        camera.position.set(center.x, center.y + size.y * 0.1, center.z + size.y * 2.2)
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

  return (
    <div className="m3d-viewer">
      <div ref={hostRef} className="m3d-viewer-canvas" />
      {loading && <p className="lora-hint">読み込んでいます…</p>}
      {error && <p className="lora-error">3D を読み込めませんでした: {error}</p>}
    </div>
  )
}
