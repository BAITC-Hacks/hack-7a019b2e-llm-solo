# Пайплайн «Граф денег»

Полная инструкция по запуску, эвристикам ролей, приоритету, кластеризации, ограничениям данных и масштабированию находится в [корневом README](../README.md).

Запуск из корня проекта:

```powershell
python -m pip install -r starter/requirements.txt
python starter/starter.py --data data --out out
```

Пайплайн создаёт три CSV и автономный `out/index.html`. Он не обращается к внешним API и не требует ключей.
