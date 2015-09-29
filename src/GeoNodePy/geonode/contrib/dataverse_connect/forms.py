from django import forms

class ShapefileImportDataForm(forms.Form):

    # required
    title = forms.CharField()
    abstract = forms.CharField(widget=forms.Textarea)
    dv_user_email = forms.EmailField('Dataverse user email')
    shapefile_name = forms.CharField()

    # optional
    keywords = forms.CharField(max_length=255, required=False)
    worldmap_username = forms.CharField(max_length=100, required=False)

    class Meta:
        abstract = True

    def __unicode__(self):
        return '%s (%s)' % (self.title, self.shapefile_name)

class DataverseLayerIndentityForm(forms.Form):

    dataverse_installation_name = forms.CharField(help_text='url to Harvard Dataverse, Odum Institute Dataverse, etc')
    datafile_id = forms.IntegerField(help_text='id in database')  # for API calls.
    #geonode_layer_name = forms.CharField('layer_typename')