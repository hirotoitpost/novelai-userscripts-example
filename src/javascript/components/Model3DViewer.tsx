import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

/**
 * 素体の 3D モデル(GLB)を見る。ドラッグで回す・ホイールで寄る。GLB に入った動き(骨のアニメーション)を選んで再生する。
 * 形状パーツ(GLB のモーフ = Blender のシェイプキー)には shapes の強さを当てる(書いていない形は 0)。
 * three.js は大きいので、Model3D から必要になったときだけ読み込む(React.lazy)。
 * onPick があれば、体の上をクリック(ドラッグせずに離す)した点を、休んだ姿勢(動き・形状パーツを当てる前)の位置にして
 * Blender の座標(m。Z 上・正面 -Y)で渡す(ならす所を選ぶ)。markers の所に、半透明の球を出す。
 */

/** Blender の座標(Z 上・正面 -Y)の点。GLB(Y 上・正面 +Z)とは軸が入れ替わる */
export type BlenderPoint = [number, number, number]

export interface Marker {
  at: BlenderPoint
  /** 半径(mm) */
  radius: number
}

const toBlender = (v: THREE.Vector3): BlenderPoint => [v.x, -v.z, v.y]
const fromBlender = ([x, y, z]: BlenderPoint) => new THREE.Vector3(x, z, -y)

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
  /** 体の上をクリックした点(休んだ姿勢の位置。Blender の座標)を受け取る。無ければクリックで選ばない */
  onPick?: (at: BlenderPoint) => void
  /** 印を出す所(ならす所) */
  markers?: Marker[]
}

export interface CameraView {
  position: [number, number, number]
  target: [number, number, number]
}

/** 当たった三角形の上の点を、同じ三角形の休んだ姿勢(モーフ・骨を当てる前)の位置に移す(世界の座標) */
function restPoint(hit: THREE.Intersection): THREE.Vector3 | null {
  const mesh = hit.object as THREE.Mesh
  const face = hit.face
  const position = mesh.geometry?.attributes.position
  if (!face || !position) return null
  const posed = [face.a, face.b, face.c].map(i => mesh.getVertexPosition(i, new THREE.Vector3()))
  const local = mesh.worldToLocal(hit.point.clone())
  const bary = THREE.Triangle.getBarycoord(local, posed[0], posed[1], posed[2], new THREE.Vector3())
  if (!bary) return null
  const rest = new THREE.Vector3()
  ;[face.a, face.b, face.c].forEach((i, k) => rest.addScaledVector(new THREE.Vector3().fromBufferAttribute(position, i), bary.getComponent(k)))
  return rest.applyMatrix4(mesh.matrixWorld)
}

export default function Model3DViewer({ src, animation, onAnimations, shapes, view, onPick, markers }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const mixerRef = useRef<THREE.AnimationMixer | null>(null)
  const morphRef = useRef<THREE.Mesh[]>([])
  // 着脱する部品(乳首・乳輪。part_<形>_<L|R>)。選んでいない形は隠す
  const partsRef = useRef<THREE.Object3D[]>([])
  const clipsRef = useRef<THREE.AnimationClip[]>([])
  // クリックで選ぶ(最新の onPick を、読み込み直さずに使う)
  const pickRef = useRef(onPick)
  pickRef.current = onPick
  const bodiesRef = useRef<THREE.Mesh[]>([])
  const markerGroupRef = useRef<THREE.Group | null>(null)
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
    const markerGroup = new THREE.Group()
    scene.add(markerGroup)
    markerGroupRef.current = markerGroup

    // ドラッグ(回す)と見分けるため、押した所から 5px 以内で離したときだけ選ぶ
    let down: { x: number; y: number } | null = null
    const raycaster = new THREE.Raycaster()
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY } }
    const onUp = (e: PointerEvent) => {
      const pick = pickRef.current
      if (!pick || !down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return
      const rect = renderer.domElement.getBoundingClientRect()
      const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1)
      raycaster.setFromCamera(ndc, camera)
      // 体だけ(部品・印・床は除く)
      const hit = raycaster.intersectObjects(bodiesRef.current, false)[0]
      const rest = hit ? restPoint(hit) : null
      if (rest) pick(toBlender(rest))
    }
    renderer.domElement.addEventListener('pointerdown', onDown)
    renderer.domElement.addEventListener('pointerup', onUp)

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
        // 選ぶときに当てる体(着脱する部品の中のメッシュは除く)
        const bodies: THREE.Mesh[] = []
        model.traverse(obj => {
          const mesh = obj as THREE.Mesh
          let inPart = false
          for (let o: THREE.Object3D | null = obj; o; o = o.parent) if (/^part_.+_[LR]$/.test(o.name)) inPart = true
          if (mesh.isMesh && !inPart) bodies.push(mesh)
        })
        bodiesRef.current = bodies
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
      renderer.domElement.removeEventListener('pointerdown', onDown)
      renderer.domElement.removeEventListener('pointerup', onUp)
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

  useEffect(() => {
    const group = markerGroupRef.current
    if (!group) return
    for (const child of [...group.children]) {
      group.remove(child)
      const mesh = child as THREE.Mesh
      mesh.geometry.dispose()
      ;(mesh.material as THREE.Material).dispose()
    }
    for (const marker of markers ?? []) {
      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(marker.radius / 1000, 20, 14),
        new THREE.MeshBasicMaterial({ color: 0x2f8cff, transparent: true, opacity: 0.35, depthWrite: false }),
      )
      sphere.position.copy(fromBlender(marker.at))
      group.add(sphere)
    }
  }, [markers, loading])

  return (
    <div className={`m3d-viewer${onPick ? ' m3d-viewer--picking' : ''}`}>
      <div ref={hostRef} className="m3d-viewer-canvas" />
      {loading && <p className="lora-hint">読み込んでいます…</p>}
      {error && <p className="lora-error">3D を読み込めませんでした: {error}</p>}
    </div>
  )
}
