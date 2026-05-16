"""
日本庭園を背景に日本家屋の縁側で日向ぼっこしている猫の画像を生成するスクリプト
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from novelai import NovelAI
from novelai.types import GenerateImageParams

load_dotenv()

api_key = os.getenv("NOVELAI_API_TOKEN") or os.getenv("NOVELAI_API_KEY")
if not api_key:
    raise RuntimeError("NOVELAI_API_TOKEN または NOVELAI_API_KEY が設定されていません")

output_dir = Path(__file__).parent.parent / "outputs"
output_dir.mkdir(exist_ok=True)

params = GenerateImageParams(
    prompt=(
        "photo of a mackerel tabby cat (kijitora) lying on its back on the engawa wooden veranda "
        "of a traditional Japanese house, brown tabby with black stripes, white belly exposed, "
        "round face, chubby round head, big round cheeks, cute round face cat, "
        "cat washing its face with its paw, eyes half-closed, relaxed and content, "
        "warm sunlight casting soft shadows on the wooden planks, "
        "beautiful Japanese garden (nihon teien) visible through shoji screen, "
        "stone lantern, koi pond, autumn maple tree with red leaves, raked gravel, "
        "moss covered stones, bamboo fence in background, "
        "shot on Canon EOS R5, 50mm lens, shallow depth of field, "
        "natural light photography, ultra realistic, 8k resolution, "
        "fine fur texture, bokeh background"
    ),
    negative_prompt=(
        "human, person, people, text, watermark, signature, "
        "anime, cartoon, illustration, painting, drawing, cg render, "
        "low quality, ugly, deformed, extra limbs, standing cat, walking cat"
    ),
    model="nai-diffusion-4-5-full",
    size="landscape",
    steps=28,
    scale=6.5,
    quality=True,
    uc_preset="light",
)

print("画像生成中...")
client = NovelAI(api_key=api_key)
images = client.image.generate(params)

output_path = output_dir / "cat_japanese_garden_roundface.png"
images[0].save(output_path)
print(f"保存完了: {output_path}")
