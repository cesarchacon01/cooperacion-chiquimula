import os, json, re, sys
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = Path(__file__).resolve().parent
CATALOGO = json.loads((BASE/'catalogo_xlsform.json').read_text(encoding='utf-8'))
CHOICES = CATALOGO['choices']; LUGARES = CATALOGO['lugares_poblados']
MUNICIPIOS = ['Camotán','Chiquimula','Concepción las Minas','Esquipulas','Ipala','Jocotán','Olopa','Quezaltepeque','San Jacinto','San José la Arada','San Juan Ermita']

def label(lista, valor):
    if valor in (None,''): return ''
    return CHOICES.get(lista,{}).get(str(valor), str(valor))

def labels(lista, valor):
    if not valor: return []
    vals = valor if isinstance(valor,list) else str(valor).split()
    return [label(lista,v) for v in vals if v]

def find_value(obj, name, default=None):
    if not isinstance(obj,dict): return default
    if name in obj: return obj[name]
    for k,v in obj.items():
        if k.split('/')[-1] == name and not isinstance(v,(dict,list)): return v
    for v in obj.values():
        if isinstance(v,dict):
            got=find_value(v,name,None)
            if got is not None: return got
    return default

def find_repeat(obj, name):
    if not isinstance(obj,dict): return []
    if name in obj and isinstance(obj[name],list): return obj[name]
    for k,v in obj.items():
        if k.split('/')[-1] == name and isinstance(v,list): return v
    for v in obj.values():
        if isinstance(v,dict):
            got=find_repeat(v,name)
            if got: return got
    return []

def num(v):
    """Devuelve número o None. Un campo vacío no equivale a cero."""
    if v in (None, ''): return None
    try:
        n=float(v)
        return int(n) if n.is_integer() else n
    except:
        return None

def cargar_coordenadas():
    p=BASE/'coordenadas_lugares.json'
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        print('ADVERTENCIA: coordenadas_lugares.json no pudo leerse:',e); return {}

COORDS=cargar_coordenadas()

def coord_for(code):
    c=COORDS.get(code,{})
    # Las coordenadas marcadas como "aproximada" no cuentan como georreferenciación:
    # el visor las usa solo para dibujar el punto con borde punteado.
    if c.get('aproximada'): return None, None
    return c.get('lat'), c.get('lon')

def api_get(url, token):
    req=Request(url,headers={'Authorization':f'Token {token}','Accept':'application/json','User-Agent':'cooperacion-chiquimula-github-action'})
    with urlopen(req,timeout=90) as r: return json.loads(r.read().decode('utf-8'))

def descargar():
    token=os.environ.get('KOBO_TOKEN','').strip(); uid=os.environ.get('KOBO_ASSET_UID','').strip()
    if not token or not uid: raise RuntimeError('Faltan KOBO_TOKEN o KOBO_ASSET_UID en GitHub Secrets.')
    url=f'https://eu.kobotoolbox.org/api/v2/assets/{uid}/data/?format=json&limit=3000'
    data=api_get(url,token)
    resultados=data.get('results',data if isinstance(data,list) else [])
    # Paginación defensiva
    while isinstance(data,dict) and data.get('next'):
        data=api_get(data['next'],token); resultados.extend(data.get('results',[]))
    # Kobo puede conservar metadatos de envíos eliminados; no deben llegar al visor.
    resultados=[r for r in resultados if not find_value(r,'_deleted_at',None)]
    return resultados

# Diagnóstico de lugares: se imprime en el registro de la tarea y se resume en data.json.
RECUPERADOS=[]      # lugares hallados fuera del campo estándar
SIN_LUGARES=[]      # programas con trabajo comunitario que llegan sin lugares

def codigos_en(obj):
    """Busca códigos del catálogo de lugares en cualquier campo de un envío.

    Sirve de red de seguridad: si el formulario cambió el nombre del grupo o del
    campo, o si los lugares vienen como selección múltiple, se recuperan igual.
    Devuelve pares (codigo, nombre_del_campo)."""
    hallados=[]
    def recorrer(o, campo):
        if isinstance(o,dict):
            for k,v in o.items(): recorrer(v, k.split('/')[-1])
        elif isinstance(o,list):
            for v in o: recorrer(v, campo)
        elif isinstance(o,str):
            partes=o.split()
            if partes and all(t in LUGARES for t in partes):
                hallados.extend((t,campo) for t in partes)
    recorrer(obj,'')
    return hallados

def comunidad_minima(code):
    """Lugar del catálogo sin detalle de población ni acciones (dato recuperado)."""
    meta=LUGARES.get(code,{})
    lat,lon=coord_for(code)
    return {'codigo_lugar':code,'lugar_poblado':meta.get('lugar_poblado') or code,'municipio':label('municipios',meta.get('municipio_codigo','')),'clasificacion':meta.get('clasificacion'),'viviendas_catalogo':meta.get('viviendas'),'lat':lat,'lon':lon,'fuera_catalogo':False,'estado_validacion':'Catálogo territorial','referencia_lugar':'','poblacion_meta':[],'datos_cuantitativos_poblacion':'','pm_cantidades':{},'poblacion_total_estimada':None,'accion_comunidad':[],'ac_cantidades':{},'etnia':[],'grupo_etario':[],'area':''}

def valor_grupo(rep, nombre):
    """Valor de una pregunta común del modo "varias comunidades a la vez".

    Esas preguntas viven en el programa (no en cada lugar) y su nombre lleva un
    prefijo o sufijo sobre el nombre base (p. ej. g_poblacion_meta). Se busca solo
    entre los campos directos del programa, nunca dentro de bloques repetidos."""
    exacto=None; candidato=None
    for k,v in rep.items():
        if isinstance(v,(list,dict)): continue
        corto=k.split('/')[-1]
        if corto=='g_'+nombre: return v
        if corto==nombre: exacto=v
        elif candidato is None and (corto.endswith('_'+nombre) or re.fullmatch(nombre+r'_(g|grupo|varias|multi)\w*',corto)): candidato=v
    return candidato if candidato not in (None,'') else (exacto if exacto is not None else '')

def campos_de_lugar(obj):
    """Nombres de campos que parecen referirse a lugares (solo nombres, sin valores)."""
    nombres=set()
    def recorrer(o):
        if isinstance(o,dict):
            for k,v in o.items():
                corto=k.split('/')[-1]
                if re.search(r'lugar|comun|cobert|aldea|caser|territ',corto,re.I): nombres.add(corto+(' [lista]' if isinstance(v,list) else ''))
                recorrer(v)
        elif isinstance(o,list):
            for v in o: recorrer(v)
    recorrer(obj)
    return sorted(nombres)

def programa_desde(rep, actor, extra=None):
    trabajo_comunitario=label('si_no',find_value(rep,'trabajo_comunitario',''))
    sectores=labels('sector',find_value(rep,'sector',''))
    if find_value(rep,'sector_otro'):
        sectores=[s for s in sectores if s!='Otro']+[str(find_value(rep,'sector_otro'))]
    modalidad=labels('modalidad',find_value(rep,'modalidad',''))
    if find_value(rep,'modalidad_otro'): modalidad=[x for x in modalidad if x!='Otra']+[str(find_value(rep,'modalidad_otro'))]
    fuentes=labels('fuente_financiamiento',find_value(rep,'fuente_financiamiento',''))
    if find_value(rep,'fuente_financiamiento_otro'): fuentes=[x for x in fuentes if x!='Otros']+[str(find_value(rep,'fuente_financiamiento_otro'))]
    mun_inst=labels('municipios',find_value(rep,'municipios_cobertura_institucional',''))
    mun_inst=[m for m in mun_inst if m!='Otro']
    if find_value(rep,'municipios_cobertura_institucional_otro'): mun_inst.append(str(find_value(rep,'municipios_cobertura_institucional_otro')))
    inst=labels('beneficiarios_institucionales',find_value(rep,'beneficiarios_institucionales',''))
    inst_counts={}
    inst_code_to_field={'Centros o puestos de salud':'bi_cant_centros_salud','Municipalidades':'bi_cant_municipalidades','COMRED (Coordinadora Municipal para la Reducción de Desastres)':'bi_cant_comred','COMUSAN (Comisión Municipal de Seguridad Alimentaria y Nutricional)':'bi_cant_comusan','Oficina Municipal de Juventud':'bi_cant_oficina_municipal_juventud','Dirección Municipal de Planificación (DMP)':'bi_cant_direccion_municipal_planificacion','Oficina Municipal de Desarrollo Económico Local':'bi_cant_oficina_municipal_desarrollo_economico','Oficina Municipal de Gestión Integral de Riesgo de Desastres (GIRD)':'bi_cant_oficina_municipal_gird','Oficina/Dirección Municipal de Seguridad Alimentaria y Nutricional (SAN)':'bi_cant_oficina_municipal_san'}
    for x in inst:
        if x=='Otra':
            otro=str(find_value(rep,'beneficiarios_institucionales_otro','Otro')); inst_counts[otro]=num(find_value(rep,'bi_cant_otro'))
        else: inst_counts[x]=num(find_value(rep,inst_code_to_field.get(x,''))) if inst_code_to_field.get(x) else 0
    comunidades=[]
    # Un mismo bloque puede traer varios lugares si el campo es de selección múltiple:
    # se separa en un lugar por código. Los bloques sin lugar se omiten.
    bloques=[]
    for c in find_repeat(rep,'cobertura_comunidad'):
        crudo=str(find_value(c,'lugar_poblado','') or '').strip()
        partes=crudo.split()
        if not crudo: continue
        if crudo not in LUGARES and len(partes)>1 and all(t in LUGARES for t in partes): bloques.extend((c,t) for t in partes)
        else: bloques.append((c,crudo))

    # Modo "Varias comunidades a la vez" del formulario: los lugares se marcan en una
    # sola pregunta del programa, la población y las acciones son comunes a todos, y
    # las cantidades van en el bloque repetido "cantidades_grupo" (un ítem por lugar,
    # con el código en g_lugar_i). Antes este modo no se leía y esos programas
    # quedaban sin comunidades en el mapa.
    comunes={n:valor_grupo(rep,n) for n in ('poblacion_meta','poblacion_meta_otro','datos_cuantitativos_poblacion','accion_comunidad','accion_comunidad_otro','accion_comunidad_agropecuaria_otro','etnia','grupo_etario','area')}
    for g in find_repeat(rep,'cantidades_grupo'):
        code=str(find_value(g,'g_lugar_i','') or '').strip()
        if not code: continue
        sintetico=dict(comunes)
        for k,v in g.items():
            if isinstance(v,(list,dict)): continue
            corto=k.split('/')[-1]
            if corto.startswith('g_'): corto=corto[2:]
            sintetico[corto]=v
            m=re.search(r'(pm|ac)_cant_.+$',corto)      # p. ej. g2_pm_cant_mujeres -> pm_cant_mujeres
            if m: sintetico[m.group(0)]=v
        sintetico['lugar_poblado']=code
        bloques.append((sintetico,code))
    # Lugares marcados en campos del programa que no llegaron por ninguno de los dos bloques.
    vistos={code for _,code in bloques}
    for code,campo in codigos_en({k:v for k,v in rep.items() if not isinstance(v,(list,dict))}):
        if code in vistos: continue
        vistos.add(code); sintetico=dict(comunes); sintetico['lugar_poblado']=code
        bloques.append((sintetico,code))
        RECUPERADOS.append({'organizacion':actor['organizacion'],'programa':str(find_value(rep,'nombre_programa','') or ''),'codigo_lugar':code,'campo':campo})
    # Un mismo lugar no se repite dentro del programa (salvo los "otros", que se distinguen por nombre).
    unicos=[]; repetidos=set()
    for c,code in bloques:
        if code!='otro_lugar_no_lista':
            if code in repetidos: continue
            repetidos.add(code)
        unicos.append((c,code))
    bloques=unicos
    for c,code in bloques:
        es_adicional=(code=='otro_lugar_no_lista')
        if es_adicional:
            muni_codigo=str(find_value(c,'otro_lugar_municipio','') or '')
            muni=label('municipios',muni_codigo)
            lugar=str(find_value(c,'otro_lugar_nombre','') or '').strip() or 'Lugar adicional sin nombre'
            referencia=str(find_value(c,'otro_lugar_referencia','') or '').strip()
            # Código estable para agrupar reportes del mismo nombre dentro del mismo municipio.
            slug=re.sub(r'[^a-z0-9]+','_',lugar.lower()).strip('_')
            codigo_registro=f"adicional:{muni_codigo}:{slug or 'sin_nombre'}"
            meta={}
            lat,lon=None,None
        else:
            meta=LUGARES.get(code,{})
            muni=label('municipios',meta.get('municipio_codigo',''))
            lugar=meta.get('lugar_poblado') or code
            referencia=''
            codigo_registro=code
            lat,lon=coord_for(code)
        pob=labels('poblacion_meta',find_value(c,'poblacion_meta',''))
        datos_cuantitativos=label('si_no',find_value(c,'datos_cuantitativos_poblacion',''))
        tiene_cantidades=str(find_value(c,'datos_cuantitativos_poblacion','') or '').lower()=='si'
        cantidades={}
        for pc in ['mujeres','mujeres_embarazadas_lactantes','pueblos_indigenas','migrantes_retornados','personas_discapacidad','adultos_mayores','hogares_ninez_menor_5','familias','agricultores','estudiantes','docentes','padres_cuidadores']:
            if pc in str(find_value(c,'poblacion_meta','')).split():
                cantidades[label('poblacion_meta',pc)]=num(find_value(c,'pm_cant_'+pc)) if tiene_cantidades else None
        if 'otro' in str(find_value(c,'poblacion_meta','')).split():
            cantidades[str(find_value(c,'poblacion_meta_otro','Otro'))]=num(find_value(c,'pm_cant_otro')) if tiene_cantidades else None

        acciones=[a for a in labels('accion_comunidad',find_value(c,'accion_comunidad','')) if a!='Ninguna / no aplica']
        ac_counts={}
        # COCOSAN está en el XLSForm como acción/capacidad, pero no tiene campo de cantidad.
        acciones_codigos=str(find_value(c,'accion_comunidad','') or '').split()
        for ac in ['escuelas','organizaciones_comunitarias','cooperativas','grupos_auto_ahorro_prestamo','emprendimientos','colred','cuadrillas_incendios','cader_eca','viveros_forestales','sistemas_riego']:
            if ac in acciones_codigos:
                ac_counts[label('accion_comunidad',ac)]=num(find_value(c,'ac_cant_'+ac))
        if 'infraestructura_productiva_agropecuaria' in acciones_codigos:
            nombre=str(find_value(c,'accion_comunidad_agropecuaria_otro','') or label('accion_comunidad','infraestructura_productiva_agropecuaria'))
            ac_counts[nombre]=num(find_value(c,'ac_cant_infraestructura_productiva_agropecuaria'))
        if 'cocosan' in acciones_codigos:
            ac_counts[label('accion_comunidad','cocosan')]=None
        if 'otro' in acciones_codigos:
            ac_counts[str(find_value(c,'accion_comunidad_otro','Otro'))]=num(find_value(c,'ac_cant_otro'))

        valores_cuantificados=[v for v in cantidades.values() if isinstance(v,(int,float))]
        poblacion_total_estimada=max(valores_cuantificados) if valores_cuantificados else None
        comunidades.append({'codigo_lugar':codigo_registro,'lugar_poblado':lugar,'municipio':muni,'clasificacion':meta.get('clasificacion'),'viviendas_catalogo':meta.get('viviendas'),'lat':lat,'lon':lon,'fuera_catalogo':es_adicional,'estado_validacion':'Pendiente de validación' if es_adicional else 'Catálogo territorial','referencia_lugar':referencia,'poblacion_meta':pob,'datos_cuantitativos_poblacion':datos_cuantitativos,'pm_cantidades':cantidades,'poblacion_total_estimada':poblacion_total_estimada,'accion_comunidad':acciones,'ac_cantidades':ac_counts,'etnia':labels('etnia',find_value(c,'etnia','')),'grupo_etario':labels('grupo_etario',find_value(c,'grupo_etario','')),'area':label('area',find_value(c,'area',''))})
    # Red de seguridad: lugares del catálogo presentes en otros campos del envío.
    ya={c['codigo_lugar'] for c in comunidades}
    nombre_prog=str(find_value(rep,'nombre_programa','') or '')
    for code,campo in codigos_en(rep)+(codigos_en(extra) if extra is not None else []):
        if code in ya: continue
        ya.add(code); comunidades.append(comunidad_minima(code))
        RECUPERADOS.append({'organizacion':actor['organizacion'],'programa':nombre_prog,'codigo_lugar':code,'campo':campo})
    if trabajo_comunitario=='Sí' and not comunidades:
        SIN_LUGARES.append({'organizacion':actor['organizacion'],'programa':nombre_prog,'campos_de_lugar':campos_de_lugar(extra if extra is not None else rep)})
    return {'organizacion':actor['organizacion'],'tipo_actor':actor['tipo_actor'],'trabajo_comunitario':trabajo_comunitario,'pais_origen':actor['pais_origen'],'contacto':actor['contacto'],'nombre_programa':str(find_value(rep,'nombre_programa','')),'tipo_atencion':label('tipo_atencion',find_value(rep,'tipo_atencion','')),'sector':sectores,'modalidad':modalidad,'fecha_inicio':str(find_value(rep,'fecha_inicio','') or ''),'tipo_duracion':label('tipo_duracion',find_value(rep,'tipo_duracion','')),'fecha_fin':str(find_value(rep,'fecha_fin','') or ''),'duracion_aproximada':str(find_value(rep,'duracion_aproximada_texto','') or ''),'estado_programa':label('estado_programa',find_value(rep,'estado_programa','')),'contraparte_gubernamental':str(find_value(rep,'contraparte_gubernamental','') or ''),'monto_rango':label('monto_rango',find_value(rep,'monto_rango','')),'monto_rango_codigo':str(find_value(rep,'monto_rango','') or ''),'fuente_financiamiento':fuentes,'espacios_coordinacion':labels('espacios_coordinacion',find_value(rep,'espacios_coordinacion','')),'disposicion_coordinar':label('disposicion_coordinar',find_value(rep,'disposicion_coordinar','')),'beneficiarios_institucionales':inst_counts,'municipios_cobertura_institucional':mun_inst,'comunidades':comunidades}

def construir(registros):
    programas=[]
    for r in registros:
        actor={'organizacion':str(find_value(r,'nombre_organizacion','') or ''),'tipo_actor':label('tipo_actor',find_value(r,'tipo_actor','')),'pais_origen':str(find_value(r,'pais_origen','') or ''),'contacto':str(find_value(r,'correo_contacto','') or '')}
        reps=find_repeat(r,'programa_proyecto')
        if not reps and find_value(r,'nombre_programa'): reps=[r]
        for rep in reps:
            # Si el envío tiene un solo programa, también se revisa el envío completo:
            # los lugares podrían estar fuera del bloque del programa.
            p=programa_desde(rep,actor,r if len(reps)==1 else None)
            if p['nombre_programa'] or p['organizacion']: programas.append(p)
    return agregar(programas,len(registros))

def agregar(programas, n_registros):
    """Calcula los resúmenes de data.json a partir de la lista de programas."""
    resumen={m:{'organizaciones':[],'total_programas':0,'comunidades_cubiertas':0,'comunidades_total_catalogo':0,'inversion_estimada':0,'sectores':{},'estado':{},'instituciones':{},'fuente_financiamiento':{}} for m in MUNICIPIOS}
    for meta in LUGARES.values():
        m=label('municipios',meta.get('municipio_codigo',''))
        if m in resumen: resumen[m]['comunidades_total_catalogo']+=1
    matriz={}; inst_res={}; pop_acc={}; dup={}
    inv={'programas_con_monto':0,'programas_sin_monto':0,'nota_metodologica':'La inversión se conserva como rango reportado. No se calculan puntos medios ni se distribuyen montos entre municipios o sectores.'}
    for p in programas:
        munis=set([c['municipio'] for c in p['comunidades'] if c['municipio']] + p['municipios_cobertura_institucional'])
        tiene_monto=bool(p.get('monto_rango_codigo'))
        if tiene_monto: inv['programas_con_monto']+=1
        else: inv['programas_sin_monto']+=1
        for s in p['sector']:
            matriz.setdefault(s,{})
            for m in munis: matriz[s][m]=matriz[s].get(m,0)+1
        for m in munis:
            if m not in resumen: continue
            rr=resumen[m]; rr['total_programas']+=1
            if p['organizacion'] and p['organizacion'] not in rr['organizaciones']: rr['organizaciones'].append(p['organizacion'])
            for s in p['sector']: rr['sectores'][s]=rr['sectores'].get(s,0)+1
            if p['estado_programa']: rr['estado'][p['estado_programa']]=rr['estado'].get(p['estado_programa'],0)+1
            for f in p['fuente_financiamiento']: rr['fuente_financiamiento'][f]=rr['fuente_financiamiento'].get(f,0)+1
        for m in p['municipios_cobertura_institucional']:
            if m in resumen:
                for typ,n in p['beneficiarios_institucionales'].items():
                    if typ not in resumen[m]['instituciones']: resumen[m]['instituciones'][typ]={'registros':0,'cantidad':0,'con_cantidad':False}
                    z=resumen[m]['instituciones'][typ]; z['registros']+=1
                    if isinstance(n,(int,float)): z['cantidad']+=n; z['con_cantidad']=True
        for typ,n in p['beneficiarios_institucionales'].items():
            if typ not in inst_res: inst_res[typ]={'registros':0,'cantidad':0,'con_cantidad':False}
            z=inst_res[typ]; z['registros']+=1
            if isinstance(n,(int,float)): z['cantidad']+=n; z['con_cantidad']=True
        for c in p['comunidades']:
            for po in c['poblacion_meta']:
                pop_acc.setdefault(po,{})
                for ac in c['accion_comunidad']: pop_acc[po][ac]=pop_acc[po].get(ac,0)+1
            for s in p['sector']:
                key=(c['codigo_lugar'],c['municipio'],s); dup.setdefault(key,set()).add(p['organizacion'])
    for m,rr in resumen.items():
        rr['organizaciones'].sort()
        rr['comunidades_cubiertas']=len({c['codigo_lugar'] for p in programas for c in p['comunidades'] if c['municipio']==m and not c.get('fuera_catalogo')})
        rr['lugares_adicionales_reportados']=len({c['codigo_lugar'] for p in programas for c in p['comunidades'] if c['municipio']==m and c.get('fuera_catalogo')})
    nombre_por_codigo={c['codigo_lugar']:c['lugar_poblado'] for p in programas for c in p['comunidades']}
    duplicidad=[{'lugar_poblado':LUGARES.get(k[0],{}).get('lugar_poblado',nombre_por_codigo.get(k[0],k[0])),'municipio':k[1],'sector':k[2],'organizaciones':sorted(v)} for k,v in dup.items() if len(v)>1]
    covered={c['codigo_lugar'] for p in programas for c in p['comunidades'] if not c.get('fuera_catalogo')}
    sin=[]
    for code,meta in LUGARES.items():
        if code in covered: continue
        lat,lon=coord_for(code)
        if lat is not None and lon is not None: sin.append({'codigo_lugar':code,'lugar_poblado':meta['lugar_poblado'],'municipio':label('municipios',meta['municipio_codigo']),'lat':lat,'lon':lon})
    adicionales={}
    for p in programas:
        for c in p['comunidades']:
            if not c.get('fuera_catalogo'): continue
            key=c['codigo_lugar']
            if key not in adicionales:
                adicionales[key]={'codigo_lugar':key,'lugar_poblado':c['lugar_poblado'],'municipio':c['municipio'],'referencia_lugar':c.get('referencia_lugar',''),'estado_validacion':'Pendiente de validación','organizaciones':set(),'programas':set()}
            if p.get('organizacion'): adicionales[key]['organizaciones'].add(p['organizacion'])
            if p.get('nombre_programa'): adicionales[key]['programas'].add(p['nombre_programa'])
    adicionales_lista=[]
    for a in adicionales.values():
        a['organizaciones']=sorted(a['organizaciones']); a['programas']=sorted(a['programas']); adicionales_lista.append(a)
    adicionales_lista.sort(key=lambda x:(x['municipio'],x['lugar_poblado']))

    return {'generado':datetime.now(timezone.utc).isoformat(),'nota':'Datos sincronizados automáticamente desde KoboToolbox. La base territorial oficial se mantiene separada de los lugares adicionales reportados pendientes de validación.','programas':programas,'municipios':MUNICIPIOS,'resumen_municipio':resumen,'matriz_sector_municipio':matriz,'comunidades_sin_cobertura':sin,'lugares_adicionales_reportados':adicionales_lista,'duplicidad':duplicidad,'instituciones_resumen':inst_res,'matriz_poblacion_accion':pop_acc,'inversion':inv,'control':{'registros_kobo':n_registros,'programas_procesados':len(programas),'lugares_catalogo':len(LUGARES),'lugares_con_coordenadas':len([c for c in COORDS.values() if not c.get('aproximada')]),'lugares_adicionales_reportados':len(adicionales_lista),'lugares_recuperados_de_otros_campos':len(RECUPERADOS),'programas_comunitarios_sin_lugares':len(SIN_LUGARES)}}

def main():
    try:
        regs=descargar(); print(f'Registros Kobo descargados: {len(regs)}')
        data=construir(regs)
        (BASE/'data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f"Programas/proyectos procesados: {len(data['programas'])}")
        # Diagnóstico de lugares (solo nombres de campos, sin datos personales).
        if RECUPERADOS:
            print(f'AVISO: {len(RECUPERADOS)} lugar(es) se recuperaron de campos no estándar:')
            for x in RECUPERADOS: print(f"  - {x['organizacion']} | {x['programa']} | {x['codigo_lugar']} (campo: {x['campo']})")
        if SIN_LUGARES:
            print(f'AVISO: {len(SIN_LUGARES)} programa(s) indican trabajo comunitario pero no traen lugares poblados:')
            for x in SIN_LUGARES: print(f"  - {x['organizacion']} | {x['programa']} | campos de lugar en el envío: {', '.join(x['campos_de_lugar']) or 'ninguno'}")
        print('data.json actualizado correctamente.')
    except Exception as e:
        print('ERROR:',e,file=sys.stderr); raise
if __name__=='__main__': main()