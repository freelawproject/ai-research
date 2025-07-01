import json
from cl.search.models import Court
from django.core.serializers.json import DjangoJSONEncoder

data = list(Court.objects.values())

with open("courts.json", "w") as f:
    json.dump(data, f, cls=DjangoJSONEncoder)
