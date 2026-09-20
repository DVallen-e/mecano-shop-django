from django.shortcuts import render

def index(request):
    return render(request, "index.html")


# work on that vezi ca trebuie sa facem user auth...
# userul nelogat poate sa vada lista de produse dar daca apasa pe el 
# ar trebui sa fie redicrtionat catre o pagina de authsi dupa ce isi face auth sa poata cumpara also
# trebuie sa integram recapha, e ceva pip django-recaptha...fa unpic de research
