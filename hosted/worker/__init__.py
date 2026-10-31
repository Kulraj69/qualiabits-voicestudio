"""The worker side of the hosted control plane.

``hosted/worker/runner.py`` is the process; this package exists so the control
plane and the worker can share one database and one object store without the plane
importing worker code (the plane serves HTTP; it does not synthesise).
"""