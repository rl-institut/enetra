# Django-oemof

django-oemof ist ein wrapper für oemof, basierend auf oemof-tabular.
>  Das Modellierungsframework oemof (Open Energy Modelling Framework) ist ein Werkzeug zur Modellierung und Analyse von Energiesystemen.

oemof nutzt im hintergrund pyomo als wrapper für verschiedene Linear Programming (LP), Mixed Integer Programming (MIP) Solver, z.b. Gurobi oder den open source [cbc solver]https://github.com/coin-or/Cbc) .

## Optimierungsstatus
Die Lösung des aufgestellen Gleichungssystems wird durch einen Solver erreicht. Dieser nutzt dabei unterschiedliche Optimierungenstrategien.
Der Solver versucht das Optimum zur Minimierung der Zielfunktion, sowie der Einhaltung der Randbedingungen zu finden.
Dazu wird intern ein [duales problem ](https://en.wikipedia.org/wiki/Dual_linear_program)  aufgestellt.
- der Wert der Zielfunktion
- der Wert der primal infeasiblities
- der Wert der dual infeasiblities

Diese Werte verlaufen während der Optimierung nicht streng monoton. Desweiteren kann keine Aussage über den final erreichbaren Wert der Zielfunktion getroffen werden (oder?).
Hieraus folgt das keine robuste Aussage über den Fortschritt während der Optimierung getroffen werden kann.


Die Lösung des aufgestellen Gleichungssystems wird durch einen Solver erreicht. Dieser nutzt dabei unterschiedliche Optimierungenstrategien. Bei LP Problemen, werden 3 Ziele verfolgt.
