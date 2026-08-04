<?xml version='1.0'?>
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:marc="http://www.loc.gov/MARC21/slim"
                xmlns:bf="http://id.loc.gov/ontologies/bibframe/">

  <xsl:template match="marc:datafield[@tag='245' or (@tag='880' and substring(marc:subfield[@code='6'],1,3)='245')]" mode="work">
    <bf:title>
      <bf:Title/>
    </bf:title>
    <xsl:for-each select="marc:subfield[@code='a' or @code='b' or @code='n' or @code='p']">
      <bf:mainTitle/>
    </xsl:for-each>
  </xsl:template>

</xsl:stylesheet>
