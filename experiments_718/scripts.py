import os
import json

from cl.search.models import Docket, OpinionCluster


COURTS = ['ala',
 'alacivapp',
 'alacrimapp',
 'alaska',
 'alaskactapp',
 'almd',
 'alnd',
 'alsd',
 'ared',
 'ariz',
 'arizctapp',
 'ark',
 'arkctapp',
 'armfor',
 'azd',
 'bap10',
 'ca1',
 'ca10',
 'ca11',
 'ca2',
 'ca3',
 'ca4',
 'ca5',
 'ca6',
 'ca7',
 'ca8',
 'ca9',
 'cacd',
 'cadc',
 'caed',
 'cafc',
 'cal',
 'calctapp',
 'cand',
 'casd',
 'cod',
 'colo',
 'coloctapp',
 'conn',
 'connappct',
 'ctd',
 'dc',
 'dcd',
 'del',
 'delsuperct',
 'fiscr',
 'fla',
 'fladistctapp1',
 'fladistctapp2',
 'fladistctapp3',
 'fladistctapp4',
 'fladistctapp5',
 'flmd',
 'flnd',
 'flsd',
 'ga',
 'gactapp',
 'gand',
 'gasd',
 'guam',
 'haw',
 'hawapp',
 'hid',
 'iand',
 'idaho',
 'idahoctapp',
 'idd',
 'ill',
 'illappct',
 'ilnd',
 'ilsd',
 'ind',
 'indctapp',
 'indtc',
 'insd',
 'iowa',
 'iowactapp',
 'kan',
 'kanctapp',
 'ky',
 'kyctapp',
 'la',
 'lactapp',
 'laed',
 'lamd',
 'lawd',
 'mad',
 'mass',
 'massappct',
 'md',
 'mdctspecapp',
 'mdd',
 'me',
 'med',
 'mich',
 'michctapp',
 'mied',
 'minn',
 'minnctapp',
 'miss',
 'missctapp',
 'miwd',
 'mnd',
 'mo',
 'moctapp',
 'mont',
 'mowd',
 'msnd',
 'mssd',
 'nc',
 'ncctapp',
 'nced',
 'ncmd',
 'nd',
 'ndctapp',
 'ndd',
 'neb',
 'nebctapp',
 'ned',
 'nev',
 'nevapp',
 'nh',
 'nhd',
 'nj',
 'njd',
 'njsuperctappdiv',
 'nm',
 'nmariana',
 'nmctapp',
 'nvd',
 'ny',
 'nyappdiv',
 'nyappterm',
 'nychemungctyct',
 'nycrimctqueens',
 'nyed',
 'nygenessctyct',
 'nylivingstctyct',
 'nynassctyct',
 'nyniagaractyct',
 'nyorangectyct',
 'nyoswegoctyct',
 'nysd',
 'nystlawctyct',
 'nysullivanctyct',
 'nyulsterctyct',
 'nywd',
 'nywestchcty',
 'ohio',
 'ohnd',
 'ohsd',
 'okla',
 'oklacivapp',
 'oklacrimapp',
 'oknd',
 'okwd',
 'or',
 'orctapp',
 'pa',
 'pacommwct',
 'paed',
 'pamd',
 'pasuperct',
 'prapp',
 'prsupreme',
 'ri',
 'sc',
 'scctapp',
 'scd',
 'sd',
 'sdd',
 'tenn',
 'tenncrimapp',
 'tennctapp',
 'tex',
 'texapp',
 'texcrimapp',
 'tnmd',
 'txed',
 'txnd',
 'txsd',
 'txwd',
 'utah',
 'utahctapp',
 'utd',
 'va',
 'vactapp',
 'vaed',
 'vid',
 'virginislands',
 'vt',
 'wash',
 'washctapp',
 'wawd',
 'wis',
 'wisctapp',
 'wiwd',
 'wva',
 'wvnd',
 'wvsd',
 'wyd',
 'wyo']


def get_scotus(chunk_size=1000):
    dockets = Docket.objects.filter(court__id="scotus")
    print(f"Found {dockets.count()} SCOTUS dockets.")

    results = []

    chunks = [
        dockets[i : i + chunk_size] for i in range(0, dockets.count(), chunk_size)
    ]

    for i, chunk in enumerate(chunks):
        print(f"Processed {i}/{len(chunks)} chunks...")

        clusters = OpinionCluster.objects.filter(docket__in=chunk).select_related("docket")

        for cluster in clusters:
            results.append({
                "docket_id": cluster.docket_id,
                "docket_number": cluster.docket.docket_number,
                "cluster_id": cluster.id if cluster else None,
                "case_name": cluster.case_name if cluster else None,
                "date_filed": cluster.date_filed.isoformat() if cluster else None,
                "precedential_status": cluster.precedential_status if cluster else None,
                "num_authorities": cluster.authorities.count() if cluster else None
            })
        
    with open("cl_scotus_dockets.json", "w") as f:
        json.dump(results, f)


def get_lower_courts(chunk_size=1000, courts=COURTS):
    for court in courts:
        
        if os.path.exists(os.path.join("lower_courts", f"cl_{court}_dockets.json")):
            continue

        dockets = Docket.objects.filter(court__id=court)
        print(f"Found {dockets.count()} {court} dockets.")

        results = []

        chunks = [
            dockets[i : i + chunk_size] for i in range(0, dockets.count(), chunk_size)
        ]

        for i, chunk in enumerate(chunks):
            print(f"Processed {i}/{len(chunks)} chunks...")

            clusters = OpinionCluster.objects.filter(docket__in=chunk).select_related("docket")

            for cluster in clusters:
                results.append({
                    "docket_id": cluster.docket_id,
                    "docket_number": cluster.docket.docket_number,
                    "cluster_id": cluster.id if cluster else None,
                    "case_name": cluster.case_name if cluster else None,
                    "date_filed": cluster.date_filed.isoformat() if cluster else None,
                    "precedential_status": cluster.precedential_status if cluster else None,
                    #"num_authorities": cluster.authorities.count() if cluster else None
                })
            
        with open(f"cl_lower_courts/cl_{court}_dockets.json", "w") as f:
            json.dump(results, f)
