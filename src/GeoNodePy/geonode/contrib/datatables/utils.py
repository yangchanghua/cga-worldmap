from __future__ import print_function
import sys
import os
import os
import glob
import uuid
import csvkit
import logging
from decimal import Decimal
from csvkit import sql
from csvkit import table
from csvkit import CSVKitWriter
from csvkit.cli import CSVKitUtility

from geoserver.catalog import Catalog
from geoserver.store import datastore_from_index

import psycopg2
from psycopg2.extensions import QuotedString

from django.template.defaultfilters import slugify
from django.core.files import File
from django.conf import settings
from django.utils.translation import ugettext_lazy as _

from geonode.maps.models import Layer
from geonode.contrib.datatables.models import DataTable, DataTableAttribute, TableJoin

from .db_helper import get_datastore_connection_string

logger = logging.getLogger('geonode.contrib.datatables.utils')


def process_csv_file(instance, delimiter=",", no_header_row=False):
    csv_filename = instance.uploaded_file.path
    table_name = slugify(unicode(os.path.splitext(os.path.basename(csv_filename))[0])).replace('-','_')
    if table_name[:1].isdigit():
        table_name = 'x' + table_name

    instance.table_name = table_name
    instance.save()
    f = open(csv_filename, 'rb')
    
    print ('process_csv_file 1')
   
    try: 
        csv_table = table.Table.from_csv(f,name=table_name, no_header_row=no_header_row, delimiter=delimiter)
    except:
        instance.delete()
        return None, str(sys.exc_info()[0])

    csv_file = File(f)
    f.close()
    print ('process_csv_file 2')

    try:
        for column in csv_table:
            #print ('column', column)
            column.name = slugify(unicode(column.name)).replace('-','_')
            #print ('column.name ', column.name )

            attribute, created = DataTableAttribute.objects.get_or_create(datatable=instance, 
                attribute=column.name, 
                attribute_label=column.name, 
                attribute_type=column.type.__name__, 
                display_order=column.order)
    except:
        instance.delete()
        return None, str(sys.exc_info()[0])


    print ('process_csv_file 3')
    # Create Database Table
    try:
        sql_table = sql.make_table(csv_table,table_name)
        create_table_sql = sql.make_create_table_statement(sql_table, dialect="postgresql")
        instance.create_table_sql = create_table_sql
        instance.save()
    except:
        instance.delete()
        return None, str(sys.exc_info()[0])

    conn = psycopg2.connect(get_datastore_connection_string())

    print ('process_csv_file 4')

    try:
        cur = conn.cursor()
        cur.execute('drop table if exists %s CASCADE;' % table_name) 
        cur.execute(create_table_sql)
        conn.commit()
    except Exception as e:
        import traceback
        traceback.print_exc(sys.exc_info())
        logger.error(
            "Error Creating table %s:%s",
            instance.name,
            str(e))
    finally:
        conn.close()

    # Copy Data to postgres
    #connection_string = "postgresql://%s:%s@%s:%s/%s" % (db['USER'], db['PASSWORD'], db['HOST'], db['PORT'], db['NAME'])
    connection_string = get_datastore_connection_string(url_format=True)
    print ('process_csv_file 5')
    
    try:
        engine, metadata = sql.get_connection(connection_string)
    except ImportError:
        return None, str(sys.exc_info()[0])

    conn = engine.connect()
    trans = conn.begin()
 
    if csv_table.count_rows() > 0:
        insert = sql_table.insert()
        headers = csv_table.headers()
        try:
            conn.execute(insert, [dict(zip(headers, row)) for row in csv_table.to_rows()])
        except:
            # Clean up after ourselves
            instance.delete() 
            return None, str(sys.exc_info()[0])

    trans.commit()
    conn.close()
    f.close()
    
    return instance, ""

def create_point_col_from_lat_lon(table_name, lat_column, lon_column):
    try:
        dt = DataTable.objects.get(table_name=table_name)
    except:
        msg = "Error: (%s) %s" % (str(e), table_name)
        return None, msg

    alter_table_sql = "ALTER TABLE %s ADD COLUMN geom geometry(POINT,4326);" % (table_name)
    update_table_sql = "UPDATE %s SET geom = ST_SetSRID(ST_MakePoint(%s,%s),4326);" % (table_name, lon_column, lat_column)
    create_index_sql = "CREATE INDEX idx_%s_geom ON %s USING GIST(geom);" % (table_name, table_name)

    try:
        #db = ogc_server_settings.datastore_db
        #db = settings.DATABASES['default'] 
        """
        conn = psycopg2.connect(
            "dbname='" +
            db['NAME'] +
            "' user='" +
            db['USER'] +
            "'  password='" +
            db['PASSWORD'] +
            "' port=" +
            db['PORT'] +
            " host='" +
            db['HOST'] +
            "'")
        """
        conn = psycopg2.connect(get_datastore_connection_string())
        
        cur = conn.cursor()
        cur.execute(alter_table_sql)
        cur.execute(update_table_sql)
        cur.execute(create_index_sql) 
        conn.commit()
        conn.close()
    except Exception as e:
        conn.close()
        msg =  "Error Creating Point Column from Latitude and Longitude %s" % (str(e[0]))
        return None, msg

    # Create the Layer in GeoServer from the table 
    try:
        cat = Catalog("http://localhost:8080/geoserver/rest",
                          "admin", "geoserver")
        workspace = cat.get_workspace("geonode")
        ds_list = cat.get_xml(workspace.datastore_url)
        datastores = [datastore_from_index(cat, workspace, n) for n in ds_list.findall("dataStore")]
        ds = None
        for datastore in datastores:
            if datastore.name == "datastore":
                ds = datastore
        ft = cat.publish_featuretype(table_name, ds, "EPSG:4326", srs="EPSG:4326")
        cat.save(ft)
    except Exception as e:
        tj.delete()
        import traceback
        traceback.print_exc(sys.exc_info())
        msg = "Error creating GeoServer layer for %s: %s" % (table_name, str(e))
        return None, msg

    # Create the Layer in GeoNode from the GeoServer Layer
    try:
        layer, created = Layer.objects.get_or_create(name=table_name, defaults={
            "workspace": workspace.name,
            "store": ds.name,
            "storeType": ds.resource_type,
            "typename": "%s:%s" % (workspace.name.encode('utf-8'), ft.name.encode('utf-8')),
            "title": ft.title or 'No title provided',
            "abstract": ft.abstract or 'No abstract provided',
            "uuid": str(uuid.uuid4()),
            "bbox_x0": Decimal(ft.latlon_bbox[0]),
            "bbox_x1": Decimal(ft.latlon_bbox[1]),
            "bbox_y0": Decimal(ft.latlon_bbox[2]),
            "bbox_y1": Decimal(ft.latlon_bbox[3])
        })
        #set_attributes(layer, overwrite=True)
    except Exception as e:
        import traceback
        traceback.print_exc(sys.exc_info())
        msg = "Error creating GeoNode layer for %s: %s" % (table_name, str(e))
        return None, msg

    return layer, ""
    

def setup_join(table_name, layer_typename, table_attribute_name, layer_attribute_name):

    # Setup the Table Join in GeoNode
    try:
        dt = DataTable.objects.get(table_name=table_name)
        layer = Layer.objects.get(typename=layer_typename)
        table_attribute = dt.attributes.get(datatable=dt,attribute=table_attribute_name)
        layer_attribute = layer.attributes.get(layer=layer, attribute=layer_attribute_name)
    except Exception as e:
        import traceback
        traceback.print_exc(sys.exc_info())
        msg = "Error: (%s) %s:%s:%s:%s" % (str(e), table_name, layer_typename, table_attribute_name, layer_attribute_name)
        return None, msg

    layer_name = layer.typename.split(':')[1]
    
    view_name = "join_%s_%s" % (layer_name, dt.table_name)

    view_sql = 'create view %s as select %s.the_geom, %s.* from %s inner join %s on %s."%s" = %s."%s";' %  (view_name, layer_name, dt.table_name, layer_name, dt.table_name, layer_name, layer_attribute.attribute, dt.table_name, table_attribute.attribute)
    #view_sql = 'create materialized view %s as select %s.the_geom, %s.* from %s inner join %s on %s."%s" = %s."%s";' %  (view_name, layer_name, dt.table_name, layer_name, dt.table_name, layer_name, layer_attribute.attribute, dt.table_name, table_attribute.attribute)
    
    #double_view_name = "view_%s" % view_name
    #double_view_sql = "create view %s as select * from %s" % (double_view_name, view_name)
    
    # Retrieve stats
    #
    matched_count_sql = 'select count(%s) from %s where %s.%s in (select "%s" from %s);' % (table_attribute.attribute, dt.table_name, dt.table_name, table_attribute.attribute, layer_attribute.attribute, layer_name)
    unmatched_count_sql = 'select count(%s) from %s where %s.%s not in (select "%s" from %s);' % (table_attribute.attribute, dt.table_name, dt.table_name, table_attribute.attribute, layer_attribute.attribute, layer_name)
    unmatched_list_sql = 'select %s from %s where %s.%s not in (select "%s" from %s) limit 100;' % (table_attribute.attribute, dt.table_name, dt.table_name, table_attribute.attribute, layer_attribute.attribute, layer_name)
    
    # Create a TableJoin object
    #
    tj, created = TableJoin.objects.get_or_create(source_layer=layer,datatable=dt, table_attribute=table_attribute, layer_attribute=layer_attribute)#, view_name=double_view_name)
    tj.view_sql = view_sql

    # Create the View (and double view)
    try:
        """
        db = settings.DATABASES['default'] 
        conn = psycopg2.connect(
            "dbname='" +
            db['NAME'] +
            "' user='" +
            db['USER'] +
            "'  password='" +
            db['PASSWORD'] +
            "' port=" +
            db['PORT'] +
            " host='" +
            db['HOST'] +
            "'")
        """
        conn = psycopg2.connect(get_datastore_connection_string())
            
        cur = conn.cursor()
        #cur.execute('drop view if exists %s;' % double_view_name)  # removing double view
        cur.execute('drop view if exists %s;' % view_name) 
        #cur.execute('drop materialized view if exists %s;' % view_name) 
        print ('view_sql', view_sql)
        cur.execute(view_sql)
        #cur.execute(double_view_sql)
        cur.execute(matched_count_sql)
        tj.matched_records_count = cur.fetchone()[0]
        cur.execute(unmatched_count_sql)
        tj.unmatched_records_count = int(cur.fetchone()[0])
        cur.execute(unmatched_list_sql)
        tj.unmatched_records_list = ",".join([r[0] for r in cur.fetchall()])
        conn.commit()
        conn.close()
    except Exception as e:
        if conn:
            conn.close()
        tj.delete()
        import traceback
        traceback.print_exc(sys.exc_info())
        msg =  "Error Joining table %s to layer %s: %s" % (table_name, layer_typename, str(e[0]))
        return None, msg

    """
    # comment out b/c of double_view_name
    
    
    # Create the Layer in GeoServer from the view
    try:
        cat = Catalog("http://localhost:8080/geoserver/rest",
                          "admin", "geoserver")
        workspace = cat.get_workspace('geonode')
        ds_list = cat.get_xml(workspace.datastore_url)
        datastores = [datastore_from_index(cat, workspace, n) for n in ds_list.findall("dataStore")]
        ds = None
        for datastore in datastores:
            if datastore.name == "geonode_imports":
                ds = datastore
        logger.error(str(ds))
        # What about this?
        ft = cat.publish_featuretype(double_view_name, ds, layer.srs, srs=layer.srs)
        cat.save(ft)
    except Exception as e:
        tj.delete()
        import traceback
        traceback.print_exc(sys.exc_info())
        msg = "Error creating GeoServer layer for %s: %s" % (view_name, str(e))
        return None, msg
    """

    # Create the Layer in GeoNode from the GeoServer Layer
    try:
        layer, created = Layer.objects.get_or_create(name=view_name, defaults={
            "workspace": workspace.name,
            "store": ds.name,
            "storeType": ds.resource_type,
            "typename": "%s:%s" % (workspace.name.encode('utf-8'), ft.name.encode('utf-8')),
            "title": ft.title or 'No title provided',
            "abstract": ft.abstract or 'No abstract provided',
            "uuid": str(uuid.uuid4()),
            #"bbox_x0": Decimal(ft.latlon_bbox[0]),
            #"bbox_x1": Decimal(ft.latlon_bbox[1]),
            #"bbox_y0": Decimal(ft.latlon_bbox[2]),
            #"bbox_y1": Decimal(ft.latlon_bbox[3])
        })
        #set_attributes(layer, overwrite=True)
        tj.join_layer = layer
        tj.save()
    except Exception as e:
        tj.delete()
        import traceback
        traceback.print_exc(sys.exc_info())
        msg = "Error creating GeoNode layer for %s: %s" % (view_name, str(e))
        return None, msg

    return tj, ""