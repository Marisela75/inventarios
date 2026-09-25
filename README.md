# Inventario con actualización bayesiana tipo Black-Litterman

Aplicación de Streamlit para analizar el archivo de existencias de septiembre de 2026. Esta es una **adaptación** de Black-Litterman a demanda física: la media histórica diaria actúa como estimación previa y una expectativa absoluta de demanda por clave actúa como vista. No interpreta demanda como rendimiento financiero ni pretende aplicar la cartera de inversión original.

## Ejecutar

1. Sube el contenido de esta carpeta a un repositorio de GitHub. Conserva `app.py`, `model.py` y `requirements.txt` en la raíz del repositorio, o configura la ruta a `app.py`.
2. Para incluir datos precargados, coloca el archivo de existencias bajo `data/existencias_ejemplo.xlsx`. También puedes mantener el Excel privado y subirlo desde la interfaz en cada sesión.
3. En [Streamlit Community Cloud](https://share.streamlit.io/) crea una aplicación con el repositorio y `app.py` como archivo principal. También puedes ejecutar localmente `pip install -r requirements.txt` y `streamlit run app.py`.

## Criterios configurados

- Hojas: `ENVASE-AUTO Y PARTES TECNICAS` y `TAPAS`. Para tapas se cuentan **solo salidas de ventas**.
- Ventana máxima: 12 meses, limitada a las fechas observadas en las hojas seleccionadas. Las celdas vacías de salidas en esas fechas cuentan como cero; el promedio de días positivos se presenta por separado y la demanda diaria se obtiene como frecuencia de movimiento multiplicada por ese promedio.
- Vista manual opcional: demanda diaria esperada por clave; confianza fija 50 %. Solo claves con al menos una salida positiva y existencia actual válida son elegibles.
- Cobertura: reposición de 15 días más revisión cada 7 días, servicio 95 %. Inventario objetivo = techo de demanda posterior por 22 días más 1.645 desviaciones estándar de la demanda futura estimada.
- Excedente = máximo(0, existencia actual − objetivo). No se recomienda una venta automática.
- Capacidad: `CAPACIDAD ALMACÉN` agrupa posiciones por presentación. El archivo no muestra una relación inequívoca entre presentación, piezas por posición y cada clave elegible. Por ello **no se ejecuta una asignación sujeta a capacidad** y todos los resultados se identifican como preliminares. No se suman espacios de categorías incompatibles ni se inventan equivalencias. El módulo contiene una rutina de asignación por categoría para cuando se disponga de un mapeo verificable.
- Errores `#REF!`, existencia negativa, salidas erróneas y claves duplicadas se excluyen con motivo. Un error en una fecha antigua fuera del cálculo no causa exclusión.

## Alcance estadístico

La demanda diaria de cada clave se considera independiente de las otras claves mediante una matriz de covarianza diagonal regularizada. Esto evita invertir una matriz inestable cuando hay pocas fechas y muchos productos. La actualización gaussiana usa `tau=1/n_días`; la incertidumbre de cada vista es `tau × varianza × (1−confianza)/confianza`. El nivel de servicio es una aproximación normal y puede requerir revisión con series intermitentes o pocos días de observación. Sin costos unitarios, el modelo solo cuantifica unidades y espacios, no pesos mexicanos. El archivo histórico fuera de las hojas seleccionadas puede tener estructuras y fechas distintas; la aplicación no combina automáticamente registros sin una unión inequívoca de clave y fecha.
