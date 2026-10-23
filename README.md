# renewables

Renewable Energy and Grid Integration

## Almanya: Duck Curve & Dunkelflaute dashboard'u

Almanya elektrik sisteminde üç şeyi gösteren tek sayfalık interaktif bir dashboard:

- **Ördek eğrisi:** ay seçerek yıllara göre ortalama günlük net yük (tüketim − güneş − rüzgâr).
- **Negatif fiyatlı saatler:** gün öncesi piyasasında (DE-LU) yıl bazında.
- **Dunkelflaute:** günlük rüzgâr+güneş üretiminin son 365 günün ortalamasına oranı. Eşik ve
  minimum süre kaydırıcılarla ayarlanıyor, olaylar grafikte ve tabloda listeleniyor.

Veri kaynağı: [Energy-Charts.info](https://energy-charts.info) (Fraunhofer ISE), CC BY 4.0.

### Nasıl çalışıyor?

```
GitHub Actions (her gün)  →  pipeline/build.py
  Energy-Charts API  →  data/hourly/YYYY-MM.csv (saatlik arşiv, 2015'ten beri)
                     →  docs/data.json (dashboard'un okuduğu özet)
GitHub Pages  →  docs/index.html
```

İlk çalıştırma 2015'ten bu yana tüm ayları çeker. Sonraki çalıştırmalar yalnızca son iki ayı yeniler.

### Yerelde çalıştırma

```bash
pip install -r requirements.txt
python -m pytest -q
python -m pipeline.build              # veriyi çek + docs/data.json üret
python -m http.server -d docs 8000    # http://localhost:8000
```

### Yayına alma

1. `Veriyi güncelle` workflow'unu Actions sekmesinden bir kez elle çalıştırın (`Run workflow`).
2. Settings → Pages → *Deploy from a branch* → `main` / `/docs`.
