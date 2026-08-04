<?xml version='1.0'?>
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:marc="http://www.loc.gov/MARC21/slim"
                xmlns:bf="http://id.loc.gov/ontologies/bibframe/">

  <xsl:template match="marc:controlfield[@tag='008']" mode="work">
    <xsl:variable name="lang" select="substring(.,36,3)"/>
    <xsl:variable name="placeChar" select="substring(.,16,1)"/>
    <bf:language>
      <xsl:value-of select="$lang"/>
    </bf:language>
  </xsl:template>

</xsl:stylesheet>
