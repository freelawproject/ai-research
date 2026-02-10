import os
import json

from cl.search.models import Docket, OpinionCluster


COURTS = ['akd',
 'almd',
 'alnd',
 'alsd',
 'ared',
 'arwd',
 'azd',
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
 'canalzoned',
 'cand',
 'casd',
 'cavc',
 'cc',
 'cit',
 'cod',
 'ctd',
 'dcd',
 'ded',
 'flmd',
 'flnd',
 'flsd',
 'gamd',
 'gand',
 'gasd',
 'gud',
 'hid',
 'iand',
 'iasd',
 'idd',
 'ilcd',
 'ilnd',
 'ilsd',
 'innd',
 'insd',
 'ksd',
 'kyed',
 'kywd',
 'laed',
 'lamd',
 'lawd',
 'mad',
 'mdd',
 'med',
 'mied',
 'miwd',
 'mnd',
 'moed',
 'mowd',
 'msnd',
 'mssd',
 'mtd',
 'nced',
 'ncmd',
 'ncwd',
 'ndd',
 'ned',
 'nhd',
 'njd',
 'nmd',
 'nmid',
 'nvd',
 'nyed',
 'nynd',
 'nysd',
 'nywd',
 'ohnd',
 'ohsd',
 'oked',
 'oknd',
 'okwd',
 'ord',
 'paed',
 'pamd',
 'pawd',
 'prd',
 'rid',
 'scd',
 'sdd',
 'tned',
 'tnmd',
 'tnwd',
 'txed',
 'txnd',
 'txsd',
 'txwd',
 'utd',
 'vaed',
 'vawd',
 'vid',
 'vtd',
 'waed',
 'wawd',
 'wied',
 'wiwd',
 'wvnd',
 'wvsd',
 'wyd']


def get_courts(chunk_size=1000, courts=COURTS):
    for court in courts:
        
        if os.path.exists(os.path.join("cl_courts", f"cl_{court}_dockets.json")):
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
            
        with open(f"cl_courts/cl_{court}_dockets.json", "w") as f:
            json.dump(results, f)
