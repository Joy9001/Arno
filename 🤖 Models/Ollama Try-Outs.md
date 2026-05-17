# Models to Try — Checklist

Pull, chat with it, see how it feels. Tick off as you go.

## Already on the laptop — try these first (no download needed)

- [ ] **Granite 3B** — fastest one. Snappy, very short replies. Good first test.
  `ollama run granite4.1:3b`
- [ ] **Nemotron 4B** — middle ground. Decent speed, more talkative than Granite.
  `ollama run nemotron-3-nano:4b`
- [ ] **Qwen 4B** — slower than the other two but supposedly the most accurate at following tool/function instructions.
  `ollama run qwen3.5:4b`

## Worth pulling — small downloads

- [ ] **Phi-4 Mini** (~2.5 GB) — small but strong at math. Good for "can I afford this?" calculations.
  `ollama pull phi4-mini:3.8b`

## Worth pulling — medium downloads

- [ ] **Mistral NeMo 12B** (~7 GB) — bigger brain, will be slow but might handle multi-step planning better.
  `ollama pull mistral-nemo:12b`

## Worth pulling — big download, **highest potential payoff**

- [ ] **Gemma 4 26B** (~18 GB) — Google's newest, released April 2026. On paper it should be the best quality of the lot **and** still fast on your laptop because it only uses a small piece of itself at a time. **This is the one I most want to know about.**
  `ollama pull gemma4:26b`
- [ ] **Qwen 30B** (~19 GB) — same idea as Gemma 4 26B, different brand. Pull this as a backup if Gemma's chat tone feels wrong.
  `ollama pull qwen3:30b`

## Already on the laptop — please delete

- [ ] **Remove the 9B Qwen** — too slow on your hardware. Takes 90 seconds for one reply. Not usable.
  `ollama rm qwen3.5:9b`
- [ ] **Remove the "abliterated" Qwen** — community-modified version that strips out the model's safety training. Not what you want for a finance assistant; also tends to be sloppy at following formatted instructions.
  `ollama rm huihui_ai/qwen3.5-abliterated:4b-Claude`

## Keep as-is

- [ ] **Nomic Embed Text** — leave it. It's not a chat model; it's the thing that turns text into searchable numbers. You'll need it later for memory and search.

---

## What to ask each model

Same five questions for every model, so you can compare apples-to-apples:

1. *"I spent 450 on Swiggy"* — does it understand the casual phrasing and pull out the amount cleanly?
2. *"₹1.5 lakh for the Goa trip in May"* — does it know "1.5 lakh" means 150,000?
3. *"Can I afford a ₹60k laptop on 6-month EMI?"* — does it ask the right follow-up questions, or just guess?
4. *"Auto bhaiya ko 150 diye"* — does it handle Hindi-English mix at all?
5. *"Split a ₹1200 dinner three ways"* — does it correctly figure out your share is ₹400?

## How to record the results

For each model, jot down:
- **Speed feel** — instant / acceptable / annoying / unusable
- **Understood my casual phrasing?** — yes / mostly / no
- **Got the rupee amounts right?** — yes / mostly / no
- **Handled Hinglish?** — yes / a bit / no
- **Vibes** — warm / neutral / robotic / preachy

Pick the one that gets ≥3 yeses and feels warm. That's your daily driver.

---

## Notes for later

- The 4 GB graphics card is the bottleneck, not the 24 GB of regular RAM. That's why some big models are surprisingly fast (they're designed to use only a small piece at a time) and some small models are slow (they have extra weight from image/video features you don't need).
- If a model feels close but talks too much, try setting `temperature` to `0` in Ollama settings — it makes them more terse.
- Save the winner's name in the Infrastructure doc once you've decided.
