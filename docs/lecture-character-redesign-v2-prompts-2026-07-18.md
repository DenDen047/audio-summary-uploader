# キャラクター再設計 V2：画像生成仕様

生成方式: Codex 組み込み画像生成（identity-preserve）  
共通参照: `tmp/character_gen/style_unification/teacher_boy_unified_v1.png`

## 2026-07-18 更新方針

- ユーザー選択により、以後は **R2「保健室の取引屋」** を基準デザインとする。
- 先生の顔、低いサイドポニー、緑の目、プラム／ミント／金の配色、丸いトークンは保全する。
- 髪は根元から毛先まで深いプラム一色。インナーカラー、前髪だけの別色、毛先グラデーションは使わない。
- 先生は成人30歳。胸・くびれ・腰を明確に誇張した豊満な砂時計型にするが、高い襟のリブニットと白衣で全身を覆い、露出で特徴を作らない。
- 少年を11歳に保つ画像では、接触、性的な視線、恋愛的なポーズを入れない。関係は健全な保健医と初心者助手。
- 恋愛的な駆け引き用の派生では、少年側を19歳の専門学校生兼アルバイト助手と明記し、長ズボンと成人の身長比に変更する。

## R2-V3 共通プロンプト

```text
Use case: stylized-concept
Asset type: YouTube educational channel character concept sheet
Primary request: Redesign the existing R2 pair while preserving their faces, unified anime rendering style, core palette, and bargain-token motif. Make both characters instantly readable in a small YouTube thumbnail.
Input images: Image 1: identity, style, palette, and composition reference; preserve the teacher's face and the boy's face.
Scene/backdrop: clean warm-white studio background, no floor line, no scenery.
Teacher: clearly adult Japanese woman, age 30, calm and teasing school-nurse presence; very voluptuous exaggerated hourglass figure with a fuller bust, narrow waist, and shapely hips; fully clothed in a high-neck plum rib-knit top, professional dark skirt, and fitted white medical coat. The coat narrows at the waist and opens into gently flared tails instead of hiding her silhouette. Deep plum hair is one uniform color from roots to tips, low side ponytail, no streaks, no ombre, no colored bangs. Emerald eyes. Enlarge the plum-and-gold bow behind her head and the round antique-gold token tin so they remain visible in a thumbnail.
Assistant: keep the round face, slightly upward gaze, timid eyebrows, unpolished single-color chestnut hair, honey eyes, oversized dusty-mint hoodie, sleeves partly covering hands, slightly crooked large gold assistant badge, and token pouch. He looks gentle, insecure, and easy to persuade, but not empty or generic.
Style/medium: polished contemporary Japanese anime character design; clean confident linework, soft cel shading, identical rendering rules for both characters.
Composition/framing: teacher left, assistant right, full body, neutral reusable standing poses, visible hands and feet, no contact between characters.
Color palette: deep plum, warm white, dusty mint, antique gold, charcoal; brighter and friendlier than a dark fantasy design.
Constraints: preserve the R2 faces and relationship mood; professional clothing; readable silhouette; no text; no watermark; no logos.
Avoid: partial hair coloring, highlights that look like dyed streaks, ombre hair, excessive cleavage, lingerie, transparent clothing, fetish nurse costume, breast-focused camera angle, seductive interaction with a minor, walking pose, cropped feet, mismatched art styles.
```

## R2-V3A｜曲線強化

- 共通プロンプトへ追記:

```text
Targeted variation: stay very close to the original R2. Increase only the adult teacher's bust-waist-hip contrast by one clear step, shape the open coat around the waist, enlarge the bow by about 20 percent, and enlarge the boy's crooked assistant badge. Keep all other details conservative.
```

## R2-V3B｜記号強化（推奨）

- 共通プロンプトへ追記:

```text
Targeted variation: push the design slightly beyond safe conventional prettiness. Give the adult teacher a boldly voluptuous but fully clothed silhouette, a visibly cinched waist, broad curved lapels, flared coat tails, a bow roughly 1.5 times the original visual size, and a round token tin almost as large as her face. Strengthen the boy's oversized sleeves, timid upward gaze, crooked gold badge, and small token pouch. The two must read as “confident bargain-making nurse” and “shy but essential beginner judge” at first glance.
```

## R2-V3C｜サムネイル強化

- 共通プロンプトへ追記:

```text
Targeted variation: explore the upper limit for thumbnail impact without nudity or fetish framing. Push the teacher's adult hourglass silhouette, bow, curved coat outline, plum piping, and large token tin another step. Push the assistant's timid eyebrows, upward gaze, oversized sleeves, and crooked badge another step. Keep the teacher's head and face the same size as R2; do not make her body chibi or distort anatomy. This is a comparison extreme, not automatically the final choice.
```

## 年齢分岐

### 11歳・健全な師弟版

- 少年は小学5〜6年生、11歳、短パンとハイソックスのまま。
- 先生への気持ちは尊敬、承認欲求、年齢相応の初恋の混線として扱う。
- 先生は背伸びを見抜いて褒め方でからかうが、性的な意味づけはしない。
- キャラクターシートは接触なし・中立ポーズを維持する。

### 19歳・成人版

- 少年らしい丸顔と弱気さを残しつつ、専門学校一年・19歳と明記する。
- 身長を成人相当に上げ、フルレングスのチャコールパンツとスニーカーへ変更する。
- 先生は30歳の学内医務室職員兼、講義動画制作の外部顧問。成績評価権は持たない。
- 憧れ、恋愛感情、身体的な惹かれを本人が区別できず、先生が軽い言葉で見透かす関係を許容する。
- YouTube用の公開素材では、胸部だけの強調や露骨な台詞を避ける。

## 共通条件

- 確定済みの成人の先生と11歳の少年を、同じ一枚の中で同時生成する。
- 先生の落ち着いた余裕、低いサイドポニー、少年の丸顔、わずかな上目遣い、気弱で純朴な姿勢は維持する。
- 髪色、目色、衣装色、衣装の輪郭、象徴小道具は大きく変更してよい。
- 両者の線、虹彩、髪のハイライト、服の皺、陰影は完全に同じ描画規則にする。
- 先生を左、少年を右にした全身立ち絵。白〜明灰色背景、文字なし、接触なし、教育チャンネル向けの中立的な立ち姿。
- 各キャラに「固有の輪郭記号」「担当色」「二人で共有する記号」を一つずつ与える。
- 腰上まで切り出してYouTubeサムネイルに縮小しても識別できる色面を優先する。

## R1｜紅のコード診療室

- 関係: 診断役の先生と、専門家の思い込みを発見する初心者モニターの少年。
- 先生: 黒髪＋バーガンディのインナーカラー、琥珀の目、赤い細縁眼鏡、赤い裏地の白衣、回路聴診器。
- 少年: 灰茶髪、青緑灰の目、マスタードの大きめプルオーバー、診断タグ。
- 共有記号: 心電図から回路へ変わるパルス線。

## R2｜保健室の取引屋

- 関係: ヒントと小さな手伝いを交換する先生と、文句を言いつつ引き受ける助手。
- 先生: 濃いプラム髪、緑の目、金具付きの大きなリボン、プラム縁の白衣、丸いトークン缶。
- 少年: 栗色髪、蜂蜜色の目、くすみミントの大きめパーカー、琥珀色のトークン袋。
- 共有記号: アンティークゴールドのコイン／スタンプ。

## R3｜夜の保健室の魔女

- 関係: コマンドを「おまじない」と呼ぶ先生と、再現可能な説明へ戻す半信半疑の弟子。
- 先生: 藍髪＋ターコイズのインナーカラー、紫灰の目、三日月と小瓶の髪飾り、濃紺の医療コート、鍵ペンダント。
- 少年: 銅茶髪、濃い青緑の目、焦茶橙のニット、手作り感のあるコマンドタグ。
- 共有記号: 鍵穴と回路。

## R4｜逆転デバッグ部

- 関係: 手違いで名目上の部長になった少年と、「顧問にすぎない」と言いながら主導する先生。
- 先生: 青黒髪＋珊瑚色の前髪、金茶の目、珊瑚色の折り返しを持つ白衣、濃い青緑のスカート、診断スタンプ。
- 少年: 黒髪＋茶のハイライト、青灰の目、鮮やかな青の大きめスウェット、黄色い大ポケット、斜めについた部長バッジ。
- 共有記号: 六角形とチェックマーク。

## R5｜推奨ハイブリッド

- 参照: 統一マスター、R1、R4の3枚。
- 関係: 手違いで部長になった少年を、先生が初心者モニターに任命する。先生が実験を主導し、少年は初心者向け認定を出す権限を持つ。
- 先生: R1の黒髪＋バーガンディ、琥珀の目、赤い細縁眼鏡、赤い裏地の白衣、回路聴診器を採用。
- 少年: 灰茶髪、青灰の目、R4の鮮やかな青い大きめスウェット、黄色いポケットと部長バッジを採用。
- 共有記号: 六角形の中で心電図が回路へ変化するマークを、聴診器とバッジ／タグに限定して配置する。
